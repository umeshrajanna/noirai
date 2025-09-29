REM Start services
docker-compose up -d

REM Stop services
docker-compose down

REM Build (takes 3-5 minutes)
docker-compose build --no-cache

REM View logs
docker-compose logs -f app

REM Restart
docker-compose restart app

REM Rebuild
docker-compose build --no-cache

REM Clean everything
docker-compose down -v
docker system prune -af

REM Check status
docker-compose ps

REM Health check
curl http://localhost:8000/health