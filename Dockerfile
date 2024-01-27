FROM python:3.12-alpine
WORKDIR src
COPY src/ .
COPY requirements.txt .

RUN pip install -r requirements.txt

CMD ["python", "index.py"]
