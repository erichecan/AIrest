FROM python:3.9-slim

WORKDIR /app

# Install system dependencies (optional, but good for pg)
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose port (Cloud Run defaults to 8080, but we can config)
ENV PORT=8080

# Command to run the application
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
