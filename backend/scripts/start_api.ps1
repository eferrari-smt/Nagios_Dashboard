Set-Location "C:\Users\Enzo Ferrari\OneDrive - smartmation.com\Documents\Nagios Dashboard\backend"
& "C:\Users\Enzo Ferrari\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn app.api:app --host 0.0.0.0 --port 8000 *>> "C:\Users\Enzo Ferrari\OneDrive - smartmation.com\Documents\Nagios Dashboard\backend\logs\api.log"
