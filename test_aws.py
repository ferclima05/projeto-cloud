import os
import boto3
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")

session = boto3.Session(region_name=AWS_REGION)
s3 = session.client("s3")
dynamodb = session.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

print("=== Testando S3 ===")
resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, MaxKeys=5)
print("Objetos encontrados:", resp.get("KeyCount", 0))

print("=== Testando DynamoDB ===")
resp = table.scan(Limit=5)
print("Itens Dynamo:", len(resp.get("Items", [])))