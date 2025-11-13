# main.py
import os
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# -------------------------------------------------------------------
# Configuração de banco
# -------------------------------------------------------------------
# Em produção, troque para algo como:
# DATABASE_URL = "postgresql+psycopg2://user:password@db_host:5432/imagens"
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    tag = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------------------------------------------------
# Configuração FastAPI
# -------------------------------------------------------------------
app = FastAPI(title="Image App - OpenStack Lab")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Página com os 3 botões."""
    return templates.TemplateResponse("index.html", {"request": request})


# -------------------------------------------------------------------
# Endpoints REST usados pela página
# -------------------------------------------------------------------

DOG_API_URL = "https://dog.ceo/api/breeds/image/random"  # API pública de imagens :contentReference[oaicite:1]{index=1}


@app.post("/api/upload")
def upload_image(db: Session = Depends(get_db)):
    """
    Upload:
    - Busca uma imagem na API pública
    - Extrai uma 'tag' (raça) da URL
    - Salva no banco
    """
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

    img = Image(url=image_url, tag=tag)
    db.add(img)
    db.commit()
    db.refresh(img)

    return {"id": img.id, "url": img.url, "tag": img.tag}


@app.get("/api/images")
def list_images(db: Session = Depends(get_db)):
    """
    Listar:
    - Retorna todas as imagens cadastradas (id, tag, url).
    """
    images = db.query(Image).order_by(Image.id.desc()).all()
    return [{"id": i.id, "tag": i.tag, "url": i.url} for i in images]


@app.get("/api/images/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db)):
    """
    Mostrar:
    - Retorna os dados de uma imagem específica.
    """
    img = db.query(Image).filter(Image.id == image_id).first()
    if not img:
        return JSONResponse(status_code=404, content={"detail": "Imagem não encontrada"})
    return {"id": img.id, "tag": img.tag, "url": img.url}


# -------------------------------------------------------------------
# Ponto de entrada (opcional, para rodar local com python main.py)
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)