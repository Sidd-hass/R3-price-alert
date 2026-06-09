#!/bin/bash

# Exit immediately on error
set -e

echo "==========================================="
echo "Live NSE Option Pivots - macOS Docker Setup"
echo "==========================================="

# 1. Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo "[-] Homebrew is not installed."
    echo "[*] Please install Homebrew by running the following command in terminal:"
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo "[*] After installing Homebrew, re-run this script."
    exit 1
fi

# 2. Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "[*] Docker is not installed. Installing Docker Desktop..."
    brew install --cask docker
    echo "[+] Docker Desktop installed successfully!"
fi

# 3. Start Docker Desktop if not running
if ! docker info &> /dev/null; then
    echo "[*] Starting Docker Desktop. Please wait..."
    open -a Docker
    
    # Wait for Docker daemon to start (timeout after 2 minutes)
    TIMEOUT=120
    ELAPSED=0
    while ! docker info &> /dev/null; do
        if [ $ELAPSED -ge $TIMEOUT ]; then
            echo "[-] Error: Docker Desktop failed to start within $TIMEOUT seconds."
            echo "[-] Please launch Docker Desktop manually from Applications and re-run this script."
            exit 1
        fi
        sleep 5
        ELAPSED=$((ELAPSED + 5))
        echo "[*] Waiting for Docker daemon to become responsive... (${ELAPSED}s)"
    done
    echo "[+] Docker daemon is running!"
else
    echo "[+] Docker daemon is already running."
fi

# 4. Check for .env file
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "[*] Creating .env from .env.example..."
        cp .env.example .env
        echo "[!] Created .env file. Please edit it with your real credentials before running the container!"
    else
        echo "[-] Error: .env.example not found. Cannot create .env template."
        exit 1
    fi
fi

# 5. Initialize state JSON files (prevents Docker from creating folders for missing files)
for FILE in telegram_subscribers.json alerted_options.json; do
    if [ ! -f "$FILE" ]; then
        echo "[*] Initializing empty state file: $FILE"
        echo "[]" > "$FILE"
    fi
done

# 6. Run the application
echo "[*] Building and running the application container..."
docker compose up --build -d

echo "==========================================="
echo "[+] Setup complete! The scanner is running in the background."
echo "[*] To check logs, run: docker compose logs -f"
echo "[*] To stop the scanner, run: docker compose down"
echo "==========================================="
