"""Setup script for OBFeed."""

from setuptools import setup, find_packages
from pathlib import Path

# Read README if it exists, otherwise use a default description
readme_path = Path(__file__).parent / "README.md"
if readme_path.exists():
    long_description = readme_path.read_text(encoding="utf-8")
else:
    long_description = "Synthetic Market Feed and Orderbook Engine"

setup(
    name="obfeed",
    version="0.1.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Synthetic Market Feed Service - REST API and WebSocket market data",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/obfeed",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: Financial and Insurance Industry",
        "Topic :: Office/Business :: Financial :: Investment",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.21.0",
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "websockets>=12.0",
        "pydantic>=2.0.0",
        "python-multipart>=0.0.6",
    ],
)
