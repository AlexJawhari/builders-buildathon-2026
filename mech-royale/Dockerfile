FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py world.py giddy.py bot.py arena_grid.bin.gz arena_nav.bin.gz ./
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
