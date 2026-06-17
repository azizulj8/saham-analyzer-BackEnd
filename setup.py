"""
Script untuk membuat file .env dari .env.example
Jalankan: python setup.py
"""
import os
import shutil

def setup():
    env_example = ".env.example"
    env_file = ".env"

    if os.path.exists(env_file):
        print(f"✅ File {env_file} sudah ada")
    else:
        shutil.copy(env_example, env_file)
        print(f"✅ File {env_file} berhasil dibuat dari {env_example}")
        print()
        print("⚠️  PENTING: Edit file .env dan isi ANTHROPIC_API_KEY")
        print("   Daftar di: https://console.anthropic.com/")

    print()
    print("Setup selesai!")
    print()
    print("Langkah selanjutnya:")
    print("1. Edit .env dan isi ANTHROPIC_API_KEY")
    print("2. Jalankan: source venv/bin/activate")
    print("3. Jalankan: uvicorn main:app --reload --port 8000")
    print("4. Buka terminal lain, masuk ke folder frontend")
    print("5. Jalankan: npm run dev")
    print("6. Buka browser: http://localhost:3000")

if __name__ == "__main__":
    setup()
