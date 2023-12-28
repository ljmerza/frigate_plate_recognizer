FROM python:3.9

WORKDIR /usr/src/app

ARG TAG_NAME
ENV TAG_NAME=${TAG_NAME}

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY index.py .

ENTRYPOINT  ["python"]
CMD ["./index.py"]
