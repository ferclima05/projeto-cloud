# main.py
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
import requests
import boto3
from botocore.exceptions import ClientError

from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

# -------------------------------------------------------------------
# Configuração AWS
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")
AWS_PROFILE = os.getenv("AWS_PROFILE")  # opcional, só pro ambiente local

if not S3_BUCKET_NAME or not DYNAMODB_TABLE_NAME:
    raise RuntimeError("S3_BUCKET_NAME e DYNAMODB_TABLE_NAME devem estar definidos no .env")

# Se você estiver rodando localmente com profile (projeto-cloud-user),
# use o AWS_PROFILE. Na EC2 (com IAM Role), você NÃO define o AWS_PROFILE.
if AWS_PROFILE:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
else:
    session = boto3.Session(region_name=AWS_REGION)

s3_client = session.client("s3")
dynamodb = session.resource("dynamodb")
images_table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# -------------------------------------------------------------------
# Configuração FastAPI
# -------------------------------------------------------------------
app = FastAPI(title="Image App - AWS")

# CORS só pro seu front falar com o FastAPI.
# Não afeta PUT direto no S3 (isso é CORS do bucket).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DOG_API_URL = "https://dog.ceo/api/breeds/image/random"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Página com os 3 botões."""
    return templates.TemplateResponse("index.html", {"request": request})


# -------------------------------------------------------------------
# Endpoints REST
# -------------------------------------------------------------------

@app.post("/api/presign")
def create_presigned_upload(
    tag: str = Body(default="dog"),
    content_type: str = Body(default="image/jpeg"),
):
    """
    Gera URL pré-assinada para upload direto do cliente (browser) pro S3 via PUT.

    Retorna:
      - image_id
      - s3_key
      - upload_url

    IMPORTANTE:
    Como estamos assinando com Metadata, o cliente DEVE enviar
    os headers x-amz-meta-tag e x-amz-meta-image_id no PUT.
    """
    try:
        image_id = str(uuid.uuid4())
        safe_tag = tag.replace(" ", "_")

        # extensão baseada no content-type
        if "jpeg" in content_type:
            ext = "jpg"
        else:
            ext = content_type.split("/")[-1] if "/" in content_type else "bin"

        s3_key = f"img/{image_id}_{safe_tag}.{ext}"

        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": s3_key,
                "ContentType": content_type,
                "Metadata": {"tag": tag, "image_id": image_id},
            },
            ExpiresIn=300,  # 5 min
        )

        return {
            "image_id": image_id,
            "s3_key": s3_key,
            "upload_url": presigned_url,
        }

    except ClientError as e:
        msg = e.response["Error"]["Message"]
        print("Erro ao gerar presigned URL:", msg)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro ao gerar presigned URL: {msg}"},
        )
    except Exception as e:
        print("Erro inesperado ao gerar presigned URL:", repr(e))
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro inesperado ao gerar presigned URL: {str(e)}"},
        )


@app.post("/api/upload")
def upload_image():
    """
    Upload server-side (mantido como teste):
    - Busca uma imagem na Dog API
    - Extrai uma 'tag' (raça)
    - Faz upload dos bytes no S3
    - Lambda salva Base64 no DynamoDB

    OBS: não é mais o fluxo principal do requisito, mas é útil pra debug.
    """
    try:
        resp = requests.get(DOG_API_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        image_url = data["message"]

        parts = image_url.split("/")
        try:
            breed_part = parts[4]
            tag = breed_part.replace("-", " ")
        except Exception:
            tag = "dog"

        img_resp = requests.get(image_url, timeout=10)
        img_resp.raise_for_status()
        image_bytes = img_resp.content

        image_id = str(uuid.uuid4())
        safe_tag = tag.replace(" ", "_")
        s3_key = f"img/{image_id}_{safe_tag}.jpg"

        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/jpeg",
            Metadata={"tag": tag, "image_id": image_id},
        )

        return {"id": image_id, "tag": tag, "s3_key": s3_key}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro ao fazer upload da imagem: {str(e)}"},
        )


@app.get("/api/images")
def list_images():
    """
    Listar:
    - Lê todos os itens da tabela.
    - Usa image_key como id.
    """
    try:
        items = []
        last_evaluated_key = None

        while True:
            scan_kwargs = {
                "ProjectionExpression": "image_key, created_at",
            }
            if last_evaluated_key:
                scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

            response = images_table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        result = []
        for item in items:
            image_key = item.get("image_key")
            if not image_key:
                continue

            filename = image_key.split("/")[-1]
            tag = filename.rsplit(".", 1)[0]

            result.append(
                {
                    "id": image_key,
                    "tag": tag,
                    "created_at": item.get("created_at"),
                }
            )

        result.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return result

    except ClientError as e:
        msg = e.response["Error"]["Message"]
        print("Erro ClientError ao listar imagens:", msg)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro ao listar imagens (AWS): {msg}"},
        )
    except Exception as e:
        print("Erro inesperado ao listar imagens:", repr(e))
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro inesperado ao listar imagens: {str(e)}"},
        )


@app.get("/api/images/{image_key}")
def get_image(image_key: str):
    """
    Mostrar:
    - Busca um item no DynamoDB pela PK image_key.
    - Retorna data_url.
    """
    try:
        response = images_table.get_item(Key={"image_key": image_key})
        item = response.get("Item")
        if not item:
            return JSONResponse(
                status_code=404,
                content={"detail": "Imagem não encontrada no DynamoDB"},
            )

        base64_str = item.get("base64_data")
        if not base64_str:
            return JSONResponse(
                status_code=202,
                content={"detail": "Imagem ainda está sendo processada pelo Lambda"},
            )

        content_type = item.get("content_type", "image/jpeg")
        data_url = f"data:{content_type};base64,{base64_str}"

        filename = image_key.split("/")[-1]
        tag = filename.rsplit(".", 1)[0]

        return {
            "id": image_key,
            "tag": tag,
            "base64": base64_str,
            "data_url": data_url,
            "created_at": item.get("created_at"),
        }

    except ClientError as e:
        msg = e.response["Error"]["Message"]
        print("Erro ClientError ao buscar imagem:", msg)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro ao buscar imagem (AWS): {msg}"},
        )
    except Exception as e:
        print("Erro inesperado ao buscar imagem:", repr(e))
        return JSONResponse(
            status_code=500,
            content={"detail": f"Erro inesperado ao buscar imagem: {str(e)}"},
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)