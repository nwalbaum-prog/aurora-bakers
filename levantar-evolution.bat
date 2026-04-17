@echo off
echo ========================================
echo  Aurora Bakers - Evolution API WhatsApp
echo ========================================
echo.
echo Levantando Evolution API en puerto 8081...
docker compose up -d

echo.
echo Esperando 5 segundos para que inicie...
timeout /t 5 /nobreak >nul

echo.
echo Creando instancia "aurora-bakers"...
curl -s -X POST "http://localhost:8081/instance/create" ^
  -H "apikey: aurora_bakers_evolution_2024" ^
  -H "Content-Type: application/json" ^
  -d "{\"instanceName\": \"aurora-bakers\", \"qrcode\": true, \"integration\": \"WHATSAPP-BAILEYS\"}"

echo.
echo ========================================
echo  Ahora escanea el QR code:
echo.
echo  GET http://localhost:8081/instance/connect/aurora-bakers
echo  Header: apikey: aurora_bakers_evolution_2024
echo.
echo  O usa el endpoint del servidor:
echo  GET http://localhost:5000/whatsapp/status
echo ========================================
echo.
pause
