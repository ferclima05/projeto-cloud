import base64
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.getenv("TABLE_NAME", "ImagensBase64")
MAX_BASE64_CHUNK = int(os.getenv("BASE64_CHUNK_SIZE", "350000"))

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)


def _chunk_string(value: str, size: int) -> list[str]:
    """Quebra uma string em pedaços com limite hard de 350 kB por item DynamoDB."""
    if not value:
        return []
    return [value[i:i + size] for i in range(0, len(value), size)]


def _derive_tag(key: str, metadata: dict) -> str:
    if metadata.get("tag"):
        return metadata["tag"]
    filename = key.split("/")[-1]
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _derive_image_id(filename: str, metadata: dict) -> str:
    if metadata.get("image_id"):
        return metadata["image_id"]
    return filename.rsplit(".", 1)[0]


def lambda_handler(event, _context):
    """
    Função disparada por eventos ObjectCreated do S3.
    - Baixa a imagem,
    - converte para Base64,
    - grava metadados + payload chunkado no DynamoDB.
    """
    processed = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        # Só lidamos com a pasta esperada
        if not key.startswith("img/"):
            print(f"Ignorando objeto fora de img/: {key}")
            continue

        filename = key.split("/")[-1]

        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read()
            metadata = obj.get("Metadata", {}) or {}
            content_type = obj.get("ContentType", "application/octet-stream")
            etag = (obj.get("ETag") or "").strip('"')
            size_bytes = obj.get("ContentLength", len(body))

            encoded = base64.b64encode(body).decode("utf-8")
            chunks = _chunk_string(encoded, MAX_BASE64_CHUNK)
            chunk_count = len(chunks)

            storage_mode = "chunked" if chunk_count > 1 else "single"
            created_at = datetime.now(timezone.utc).isoformat()

            item = {
                "image_key": filename,
                "s3_key": key,
                "bucket": bucket,
                "tag": _derive_tag(key, metadata),
                "image_id": _derive_image_id(filename, metadata),
                "content_type": content_type,
                "created_at": created_at,
                "updated_at": created_at,
                "size_bytes": size_bytes,
                "base64_size": len(encoded),
                "chunk_size": MAX_BASE64_CHUNK,
                "chunk_count": chunk_count or (1 if encoded else 0),
                "storage_mode": storage_mode,
                "etag": etag,
            }

            if storage_mode == "chunked":
                item["base64_chunks"] = chunks
            else:
                item["base64_data"] = encoded

            table.put_item(Item=item)
            processed.append(filename)
            print(f"[Lambda] Imagem {key} convertida com {item['chunk_count']} chunk(s).")

        except ClientError as err:
            print(f"[AWS] Falha ao processar {key}: {err}")
            raise
        except Exception as exc:
            print(f"[Lambda] Erro inesperado ao processar {key}: {exc}")
            raise

    return {
        "statusCode": 200,
        "body": json.dumps({"processed": processed}),
    }
