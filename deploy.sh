#!/bin/bash
set -e
echo "Building image..."
podman build -t localhost/quantsignal-api:latest .
echo "Stopping old containers..."
podman-compose down || true
echo "Starting..."
podman-compose up -d
echo "Done. Health check:"
sleep 5
curl -s http://localhost:8000/api/v1/health
