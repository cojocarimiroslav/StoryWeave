from python:3.11.0
expose 8501
cmd mkdir -p /app
WORKDIR /app
copy requirements.txt ./requirements.txt
run pip install --no-cache-dir -r requirements.txt
copy . .
ENTRYPOINT ["streamlit", "run"]
CMD ["app.py"]