# main.py
import os
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import requests
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

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

import boto3
from botocore.exceptions import ClientError

# Se você estiver rodando localmente com profile (projeto-cloud-user),
# use o AWS_PROFILE. Na EC2 (com IAM Role), você NÃO define o AWS_PROFILE
# e ele cai no else.
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

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DOG_API_URL = "https://dog.ceo/api/breeds/image/random"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Página com os 3 botões."""
    return templates.TemplateResponse("index.html", {"request": request})


# -------------------------------------------------------------------
# Endpoints REST
# -------------------------------------------------------------------


@app.post("/api/upload")
def upload_image():
    """
    Upload:
    - Busca uma imagem na API pública (Dog API)
    - Extrai uma 'tag' (raça) da URL
    - Faz upload dos bytes da imagem no S3 em img/<id>.jpg
    - Lambda será disparado pelo S3 e salvará a versão Base64 no DynamoDB
    """
    try:
        # 1) Busca URL da imagem
        resp = requests.get(DOG_API_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        image_url = data["message"]

        # URL típica: https://images.dog.ceo/breeds/hound-afghan/n02088094_1003.jpg
        parts = image_url.split("/")
        try:
            breed_part = parts[4]  # ex: 'hound-afghan'
            tag = breed_part.replace("-", " ")
        except Exception:
            tag = "dog"

        # 2) Baixa os bytes da imagem
        img_resp = requests.get(image_url, timeout=10)
        img_resp.raise_for_status()
        image_bytes = img_resp.content

        # 3) Gera um ID e monta a chave no S3 (mantendo a pasta img/)
        image_id = str(uuid.uuid4())
        s3_key = f"img/{tag.replace(" ", "_")}.jpg"

        # 4) Faz upload no S3 com a tag em metadata (para o Lambda usar, se quiser)
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=image_bytes,
            ContentType="image/jpeg",
            Metadata={"tag": tag},
        )

        # IMPORTANTE:
        # Não gravamos no DynamoDB aqui.
        # Assumimos que o Lambda, ao ser disparado pelo S3, vai:
        # - Ler o objeto
        # - Converter para Base64
        # - Salvar no DynamoDB com:
        #   id = image_id
        #   tag = tag
        #   base64_data = "<string base64>"

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
    - Lê todos os itens da tabela ImagensBase64.
    - Usa image_key como id.
    - Gera uma "tag" a partir do nome do arquivo.
    """
    try:
        response = images_table.scan()
        items = response.get("Items", [])

        # Só pra depurar no terminal:
        #print("Itens DynamoDB recebidos:", items)

        result = []
        for item in items:
            image_key = item.get("image_key")
            if not image_key:
                continue

            # tag = nome do arquivo sem extensão (ex: sql-server)
            filename = image_key.split("/")[-1]
            tag = filename.rsplit(".", 1)[0]

            result.append(
                {
                    "id": image_key,   # vamos usar image_key como id
                    "tag": tag,
                }
            )

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
    - Busca um item no DynamoDB pela chave primária image_key.
    - Monta um data_url a partir de base64_data + content_type.
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

        # tag = nome do arquivo sem extensão
        filename = image_key.split("/")[-1]
        tag = filename.rsplit(".", 1)[0]

        return {
            "id": image_key,
            "tag": tag,
            "base64": base64_str,
            "data_url": data_url,
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


# -------------------------------------------------------------------
# Ponto de entrada (para rodar local com python main.py)
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)