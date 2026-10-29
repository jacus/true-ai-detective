# The official SearXNG image with our settings (JSON API on, limiter off). Its secret comes from the
# SEARXNG_SECRET environment variable. Built by compose.yaml locally and by Railway in prod.
FROM searxng/searxng:2026.10.29-cff884f98
COPY settings.yml /etc/searxng/settings.yml
