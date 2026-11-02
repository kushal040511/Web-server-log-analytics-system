# Log Analytics System - one-shot runner for Windows (PowerShell + Docker Desktop, no make needed)
#
#   powershell -ExecutionPolicy Bypass -File run_pipeline.ps1            # everything: cluster + pipeline + ML
#   powershell -ExecutionPolicy Bypass -File run_pipeline.ps1 skeleton   # cluster + pipeline only (~10 min)
#   powershell -ExecutionPolicy Bypass -File run_pipeline.ps1 ml         # ML stage only (cluster must be up)
#
# Each step is the same command as the Makefile target of the same name.
param([ValidateSet("all", "skeleton", "ml")][string]$Stage = "all")

$ErrorActionPreference = "Continue"   # native tools write progress to stderr; failures are checked by exit code
Set-Location $PSScriptRoot
$Log = Join-Path $PSScriptRoot "output\run_pipeline.log"
New-Item -ItemType Directory -Force -Path output, data, data\attacks | Out-Null
Start-Transcript -Path (Join-Path $PSScriptRoot "output\run_pipeline_transcript.txt") -Append | Out-Null

function Step($name) { Write-Host "`n== $name  ($(Get-Date -Format HH:mm:ss))" -ForegroundColor Cyan; Add-Content $Log "== $name $(Get-Date -Format s)" }
function Run([string[]]$cmd) {
    & $cmd[0] $cmd[1..($cmd.Length - 1)]
    if ($LASTEXITCODE -ne 0) { Add-Content $Log "FAILED: $($cmd -join ' ')"; Stop-Transcript | Out-Null; throw "FAILED: $($cmd -join ' ')" }
}
function Exec([string[]]$cmd) { Run (@("docker", "compose", "exec", "-T", "client") + $cmd) }
function Fetch($url, $file) {
    if (Test-Path $file) { Write-Host "  $file already present - skipping"; return }
    Write-Host "  downloading $file ..."
    Invoke-WebRequest -Uri $url -OutFile "$file.part" -UseBasicParsing
    Move-Item "$file.part" $file
}
function Gunzip($gz, $out) {
    $in = [IO.File]::OpenRead($gz); $o = [IO.File]::Create($out)
    $z = New-Object IO.Compression.GZipStream($in, [IO.Compression.CompressionMode]::Decompress)
    $z.CopyTo($o); $z.Close(); $o.Close(); $in.Close(); Remove-Item $gz
}

# ------------------------------------------------------------------ Docker
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "== Starting Docker Desktop ..."
    Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep 3; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
    if ($LASTEXITCODE -ne 0) { throw "Docker did not start. Open Docker Desktop and run this again." }
}
Write-Host "== Docker is up: $(docker --version)"

if ($Stage -ne "ml") {
    # -------------------------------------------------------------- data (make data)
    Step "data: NASA-HTTP logs"
    $mirror = "https://raw.githubusercontent.com/greymd/NASA-HTTP/main"
    foreach ($m in "Jul95", "Aug95") {
        if (-not (Test-Path "data\access_log_$m")) {
            Fetch "$mirror/NASA_access_log_$m.gz" "data\access_log_$m.gz"
            Gunzip "data\access_log_$m.gz" "data\access_log_$m"
        } else { Write-Host "  data\access_log_$m already present - skipping" }
    }

    # -------------------------------------------------------------- cluster (make build, make up)
    Step "build: image with Java 11, Hadoop 3.3.6, Spark 3.5.3 (first time ~10 min)"
    docker image inspect loganalytics/hadoop-spark:3.3.6-3.5.3 *> $null
    if ($LASTEXITCODE -eq 0 -and $env:REBUILD -ne "1") { Write-Host "  image already built - skipping (set REBUILD=1 to rebuild)" }
    else { Run @("docker", "compose", "build") }
    Step "up: start the 7 containers and wait for HDFS"
    Run @("docker", "compose", "up", "-d")
    for ($i = 0; $i -lt 60; $i++) {
        $mode = docker compose exec -T client hdfs dfsadmin -safemode get 2>$null
        if ("$mode" -match "OFF") { Write-Host "  HDFS is ready"; break }
        Start-Sleep 3
        if ($i -eq 59) { throw "HDFS did not come up - run: docker compose logs namenode" }
    }
    docker compose ps --format "table {{.Name}}`t{{.Status}}"

    # -------------------------------------------------------------- pipeline (make skeleton)
    Step "ingest: raw logs -> HDFS"
    Exec @("hdfs", "dfs", "-mkdir", "-p", "/data/weblogs/raw", "/data/weblogs/curated", "/data/weblogs/reports", "/spark-logs")
    Exec @("bash", "-c", "hdfs dfs -put -f /workspace/data/access_log_* /data/weblogs/raw/")
    Exec @("hdfs", "dfs", "-du", "-h", "/data/weblogs/raw")
    Step "etl: Spark parses the logs into Parquet";              Exec @("spark-submit", "/workspace/jobs/etl.py")
    Step "reports: 11 Spark SQL reports";                         Exec @("spark-submit", "/workspace/jobs/reports.py")
    Step "anomalies: Z-score on hourly and daily traffic";         Exec @("spark-submit", "/workspace/jobs/anomalies.py")
    Step "dashboard: export the reports for the website";          Exec @("spark-submit", "/workspace/jobs/export_dashboard.py")
    Step "fsck: every block has 2 copies";                         Exec @("hdfs", "fsck", "/data/weblogs/raw", "-files", "-blocks")
}

if ($Stage -ne "skeleton") {
    # -------------------------------------------------------------- machine learning (make ml)
    Step "ml-data: labelled attack datasets"
    Fetch "https://raw.githubusercontent.com/Morzeux/HttpParamsDataset/master/payload_full.csv" "data\attacks\payload_full.csv"
    $fw = "https://raw.githubusercontent.com/faizann24/Fwaf-Machine-Learning-driven-Web-Application-Firewall/master"
    Fetch "$fw/goodqueries.txt" "data\attacks\goodqueries.txt"
    Fetch "$fw/badqueries.txt" "data\attacks\badqueries.txt"
    Step "ml-ingest: attack data -> HDFS"
    Exec @("hdfs", "dfs", "-mkdir", "-p", "/data/attacks")
    Exec @("bash", "-c", "hdfs dfs -put -f /workspace/data/attacks/*.csv /workspace/data/attacks/*.txt /data/attacks/")
    Step "ml-prep: clean, bias fix, de-duplicate, split";          Exec @("spark-submit", "/workspace/jobs/ml_preprocess.py")
    Step "ml-tune: settings on a validation split (~15 min)";     Exec @("spark-submit", "/workspace/jobs/ml_tune.py")
    Step "ml-train: FM + 7 models, NASA scoring (~30-40 min)";    Exec @("spark-submit", "/workspace/jobs/ml_train.py")
    Step "ml-cross: train on one dataset, test on the other";     Exec @("spark-submit", "/workspace/jobs/ml_crossdataset.py")
    Step "ml-robust: 5 random splits, McNemar (~25 min)";         Exec @("spark-submit", "/workspace/jobs/ml_robustness.py")
    Step "ml-audit: categorise the NASA flags"
    Exec @("python3", "/workspace/scripts/audit_nasa_flags.py", "/workspace/output/ml/ml_results.json")
}

Step "web: restart the website"
Run @("docker", "compose", "up", "-d", "web")
Write-Host "`n== DONE  Website http://localhost:8050   HDFS http://localhost:9870   Spark http://localhost:8080" -ForegroundColor Green
Add-Content $Log "DONE $(Get-Date -Format s)"
Stop-Transcript | Out-Null
