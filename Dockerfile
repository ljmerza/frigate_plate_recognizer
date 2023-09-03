FROM python:3.9

# RUN addgroup --system app && adduser --system --group app
# USER app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY index.py .

ENTRYPOINT  ["python", "./index.py"]
