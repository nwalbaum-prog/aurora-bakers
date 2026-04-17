# sync_contacts.ps1
# Lee los contactos de Evolution API (Docker) y sincroniza el mapa LID→teléfono con Railway.
# Ejecutar: .\sync_contacts.ps1
# O programar con el Administrador de tareas de Windows.

$RAILWAY_URL = "https://web-production-40d5b.up.railway.app"
$CRON_SECRET = $env:CRON_SECRET  # o pon tu token aquí
$CONTAINER   = "evolution-aurora-bakers"
$INSTANCE    = "aurora-bakers"
$STORE_PATH  = "/evolution/store/contacts/$INSTANCE"

Write-Host "Leyendo contactos de Docker..." -ForegroundColor Cyan

# Obtener lista de archivos de contactos
$files = docker exec $CONTAINER ls $STORE_PATH 2>$null
if (-not $files) {
    Write-Host "ERROR: No se pudo listar contactos en $STORE_PATH" -ForegroundColor Red
    exit 1
}

# Construir mapa: profilePictureFilename → JID
$lidContacts   = @{}
$phoneContacts = @{}

foreach ($file in $files) {
    $content = docker exec $CONTAINER cat "$STORE_PATH/$file" 2>$null
    try {
        $contact = $content | ConvertFrom-Json
        $jid = $contact.id
        $pic = $contact.profilePictureUrl

        # Extraer nombre del archivo de la foto (parte estable de la URL)
        if ($pic -match '/([^/?]+)\?') { $picKey = $matches[1] } else { $picKey = $pic }

        if ($jid -match '@lid') {
            $lidContacts[$picKey] = $jid
        } elseif ($jid -match '@s\.whatsapp\.net') {
            $phone = $jid -replace '@s\.whatsapp\.net', ''
            $phoneContacts[$picKey] = $phone
        }
    } catch {}
}

# Cruzar por foto de perfil
$mapping = @{}
foreach ($picKey in $lidContacts.Keys) {
    if ($phoneContacts.ContainsKey($picKey)) {
        $lid   = $lidContacts[$picKey] -replace '@lid', ''
        $phone = $phoneContacts[$picKey]
        $mapping[$lid] = $phone
        Write-Host "  $lid → $phone" -ForegroundColor Green
    }
}

if ($mapping.Count -eq 0) {
    Write-Host "No se encontraron pares LID↔teléfono" -ForegroundColor Yellow
    exit 0
}

# Enviar a Railway
$body = $mapping | ConvertTo-Json
$url  = "$RAILWAY_URL/contacts/sync?token=$CRON_SECRET"

try {
    $resp = Invoke-RestMethod -Uri $url -Method POST -Body $body -ContentType "application/json"
    Write-Host "Sincronizado: $($resp.total) entradas en Railway" -ForegroundColor Green
} catch {
    Write-Host ("ERROR enviando a Railway: " + $_.Exception.Message) -ForegroundColor Red
}
