#!/usr/bin/env bash

# Check if input is provided
if [ -z "$1" ]; then
  echo "Usage: $0 \"string_to_hash\""
  exit 1
fi

input="$1"

# Generate SHA256 hash
hash=$(printf "%s" "$input" | sha256sum | awk '{print $1}')

echo "Input : $input"
echo "SHA256: $hash"