#!/bin/bash
# verify_file.sh
# Usage: ./verify_file.sh <file_path> <expected_content_fragment>

FILE=$1
FRAGMENT=$2

if [ ! -f "$FILE" ]; then
    echo "Error: File $FILE does not exist."
    exit 1
fi

if [ ! -s "$FILE" ]; then
    echo "Error: File $FILE is empty."
    exit 1
fi

if grep -q "$FRAGMENT" "$FILE"; then
    echo "Verification successful: $FILE contains expected content."
    exit 0
else
    echo "Error: $FILE does not contain the expected content fragment."
    exit 1
fi
