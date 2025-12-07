import requests
import json

BASE_URL = 'http://127.0.0.1:7777'

def test_settings():
    print("Testing /api/settings endpoint...")
    
    # Test POST (Save setting)
    print("\n1. Testing POST /api/settings...")
    try:
        response = requests.post(f"{BASE_URL}/api/settings", json={
            'key': 'test_setting',
            'value': 'true'
        })
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200 and response.json().get('success'):
            print("✅ POST request successful")
        else:
            print("❌ POST request failed")
            return
            
    except Exception as e:
        print(f"❌ Error during POST: {e}")
        return

    # Test GET (Load settings)
    print("\n2. Testing GET /api/settings...")
    try:
        response = requests.get(f"{BASE_URL}/api/settings")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                settings = data.get('settings', {})
                print(f"Settings retrieved: {len(settings)} items")
                
                if settings.get('test_setting') == 'true':
                    print("✅ GET request successful and verified saved value")
                else:
                    print(f"❌ GET request successful but value mismatch: {settings.get('test_setting')}")
            else:
                print(f"❌ GET request failed: {data.get('error')}")
        else:
            print("❌ GET request failed")
            
    except Exception as e:
        print(f"❌ Error during GET: {e}")

if __name__ == "__main__":
    test_settings()
