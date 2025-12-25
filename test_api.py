"""
Quick test script to verify API endpoints work
"""

import requests
import json

BASE_URL = "http://localhost:5001"

def test_health():
    """Test health check"""
    response = requests.get(f"{BASE_URL}/health")
    print(f"Health check: {response.status_code}")
    print(json.dumps(response.json(), indent=2))

def test_upload():
    """Test CSV upload"""
    # Create a simple test CSV
    csv_content = """address,city,state,zip
123 Atlantic Ave,Atlantic City,NJ,08401
456 Pacific Ave,Atlantic City,NJ,08401"""
    
    with open('/tmp/test.csv', 'w') as f:
        f.write(csv_content)
    
    with open('/tmp/test.csv', 'rb') as f:
        files = {'file': ('test.csv', f, 'text/csv')}
        response = requests.post(f"{BASE_URL}/api/upload", files=files)
    
    print(f"\nUpload: {response.status_code}")
    data = response.json()
    print(json.dumps(data, indent=2))
    
    return data.get('session_id')

def test_estimate(session_id):
    """Test cost estimate"""
    response = requests.get(f"{BASE_URL}/api/estimate/{session_id}")
    print(f"\nEstimate: {response.status_code}")
    print(json.dumps(response.json(), indent=2))

if __name__ == "__main__":
    print("Testing ProspectGrid API...")
    print("=" * 50)
    
    test_health()
    
    session_id = test_upload()
    
    if session_id:
        test_estimate(session_id)
    
    print("\n" + "=" * 50)
    print("Tests complete!")
