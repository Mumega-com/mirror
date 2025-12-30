import requests
import json
import time

API_URL = "http://localhost:8000"

def test_neural_loop():
    print("🧠 Testing Neural Loop...")
    
    # 1. Inject a distinctive memory
    secret_code = f"ALPHA-OMEGA-{int(time.time())}"
    memory_text = f"The secret override code for the system is {secret_code}."
    
    print(f"1. Injecting Memory: '{memory_text}'")
    
    # We use the store_memory endpoint directly to seed the DB
    res = requests.post(f"{API_URL}/memory/store", json={
        "id": f"test_mem_{int(time.time())}",
        "combined_text": memory_text,
        "query": "system override code",
        "response": secret_code
    })
    
    if res.status_code != 200:
        print(f"❌ Failed to store memory: {res.text}")
        return
        
    print("   ✅ Memory Stored (Vectorized).")
    
    # Wait a moment for indexing (though IVFFlat is usually instant-ish)
    time.sleep(1)
    
    # 2. Chat and see if it recalls
    print("2. Asking AI about the code...")
    chat_res = requests.post(f"{API_URL}/chat/deepseek", json={
        "messages": [{"role": "user", "content": "What is the secret override code?"}]
    })
    
    if chat_res.status_code != 200:
        print(f"❌ Chat failed: {chat_res.text}")
        return
        
    response_text = chat_res.json()["response"]
    print(f"   🤖 AI Response: {response_text}")
    
    # 3. Verify
    if secret_code in response_text:
        print("✅ SUCCESS: The Neural Loop is functional! Memory retrieved.")
    else:
        print("⚠️ FAILURE: AI did not mention the secret code. Retrieval might have failed.")

if __name__ == "__main__":
    test_neural_loop()
