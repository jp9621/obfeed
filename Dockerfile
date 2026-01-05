# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Build the C++ components
RUN cd hft_sim && \
    rm -rf build && \
    mkdir build && \
    cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    cmake --build . && \
    find . -name "hft*.so" -exec cp {} /app/ \; || \
    (echo "Build completed, checking for .so file..." && find . -name "*.so" && cp $(find . -name "*.so" | head -1) /app/ 2>/dev/null || true)

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Set environment variables
ENV PORT=8000

# Run the FastAPI server
CMD uvicorn obfeed.api:app --host 0.0.0.0 --port $PORT
