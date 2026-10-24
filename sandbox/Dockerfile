# The box the coach runs its Python in. Built once, then used with no network.
FROM python:3.12-slim
RUN pip install --no-cache-dir pandas==2.3.3 matplotlib==3.10.7
RUN useradd --uid 1000 --create-home coach
USER coach
WORKDIR /tmp
ENV MPLBACKEND=Agg MPLCONFIGDIR=/tmp HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=ignore
