FROM python:3.9

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY index.py .
COPY Arial.ttf .

ENTRYPOINT  ["python"]
CMD ["./index.py"]
