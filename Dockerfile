# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the current directory contents into the container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Build the C++ components
RUN cd hft_sim && \
    rm -rf build && \
    mkdir build && \
    cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    cmake --build . --config Release && \
    cp Release/hft*.pyd ../.. || cp hft*.so ../..

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Set environment variables
ENV PORT=8000

# Run gunicorn
CMD gunicorn --bind 0.0.0.0:$PORT app:server 