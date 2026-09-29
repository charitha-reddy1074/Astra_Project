#!/usr/bin/env python3
"""Load canonical framework JSON files into the database.

Reads all .json files from backend/api/data/canonical/ and imports them
into the database via the /frameworks/load-existing endpoint.

Usage:
    python scripts/load_frameworks.py [--base http://localhost:8000]
"""
import argparse
import sys
import requests
import json

def main():
    parser = argparse.ArgumentParser(description="Load canonical frameworks into database")
    parser.add_argument("--base", default="http://127.0.0.1:8000",
                        help="Backend base URL")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  Loading canonical frameworks into database")
    print("=" * 70)

    try:
        r = requests.post(f"{args.base}/frameworks/load-existing", timeout=60)
    except Exception as e:
        print(f"\n✗ Failed to connect to backend at {args.base}")
        print(f"  Error: {e}")
        sys.exit(1)

    if r.status_code not in (200, 201):
        print(f"\n✗ API returned {r.status_code}")
        print(f"  Response: {r.text[:500]}")
        sys.exit(1)

    result = r.json()
    imported = result.get("imported", 0)
    failed = result.get("failed", 0)
    frameworks = result.get("frameworks", [])

    print(f"\n✓ Imported {imported} frameworks" + (f", {failed} failed" if failed else ""))

    if frameworks:
        print("\nFramework Details:")
        for fw in frameworks:
            print(f"\n  • {fw.get('framework_name', 'Unknown')}")
            print(f"    - Code: {fw.get('framework_id', 'N/A')[:8]}")
            print(f"    - Domains: {fw.get('domains', 0)}")
            print(f"    - Controls: {fw.get('controls', 0)}")
            print(f"    - Questions: {fw.get('questions', 0)}")
            if fw.get('collection'):
                print(f"    - Chroma collection: {fw.get('collection')}")

    if result.get("failures"):
        print("\n⚠ Failures:")
        for failure in result["failures"]:
            print(f"  - {failure.get('file')}: {failure.get('error')[:80]}")

    print("\n" + "=" * 70)
    print("\nNext: Check the Manage tab in the Knowledge Base to see updated stats")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
