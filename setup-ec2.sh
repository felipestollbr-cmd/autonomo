#!/usr/bin/env bash
# ============================================================================
# Autonomo — setup da instância EC2 (Amazon Linux ou Ubuntu)
# Sobe swap, instala Docker e prepara o n8n. NÃO expõe o n8n à internet:
# o acesso é por túnel SSH. Rode DENTRO da instância, após conectar por SSH.
#   bash setup-ec2.sh
# ============================================================================
set -euo pipefail

echo "==> [1/5] Swap (t3.micro tem só 1GB de RAM)"
if ! sudo swapon --show | grep -q /swapfile; then
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "    swap de 1G criado"
else
  echo "    swap já existe, ok"
fi

echo "==> [2/5] Detectando o sistema operacional"
if command -v dnf >/dev/null 2>&1; then
  PKG=dnf; OSFAM=amazon
elif command -v apt-get >/dev/null 2>&1; then
  PKG=apt; OSFAM=ubuntu
else
  echo "    SO não reconhecido (sem dnf nem apt). Abortando."; exit 1
fi
echo "    detectado: $OSFAM"

echo "==> [3/5] Instalando Docker"
if ! command -v docker >/dev/null 2>&1; then
  if [ "$PKG" = "dnf" ]; then
    sudo dnf install -y docker
  else
    sudo apt-get update -y
    sudo apt-get install -y docker.io
  fi
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER" || true
  echo "    Docker instalado. IMPORTANTE: saia e reconecte o SSH para o grupo 'docker' valer."
else
  echo "    Docker já instalado, ok"
fi

echo "==> [4/5] Chave de criptografia do n8n"
ENVFILE="$HOME/autonomo.env"
if [ ! -f "$ENVFILE" ]; then
  KEY=$(openssl rand -hex 24)
  cat > "$ENVFILE" <<EOF
# Guarde este arquivo. Se a chave mudar, o n8n perde as credenciais salvas.
N8N_ENCRYPTION_KEY=$KEY
GENERIC_TIMEZONE=America/Sao_Paulo
# Preencha depois de criar o bot no @BotFather e descobrir seu chat id:
TELEGRAM_CHAT_ID=
EOF
  chmod 600 "$ENVFILE"
  echo "    criado $ENVFILE (chave gerada uma única vez)"
else
  echo "    $ENVFILE já existe, mantendo a chave atual"
fi

echo "==> [5/5] Subindo o n8n (somente localhost:5678)"
sudo docker rm -f n8n 2>/dev/null || true
sudo docker run -d --restart unless-stopped --name n8n \
  --env-file "$ENVFILE" \
  -p 127.0.0.1:5678:5678 \
  -v n8n_data:/home/node/.n8n \
  n8nio/n8n:latest

echo
echo "======================================================================"
echo "n8n rodando, preso ao localhost (não exposto à internet)."
echo "No SEU Mac, abra um túnel SSH e acesse http://localhost:5678 :"
echo
echo "  ssh -i ~/.ssh/autonomo-key.pem -L 5678:localhost:5678 ec2-user@SEU_IP"
echo
echo "(troque ec2-user por 'ubuntu' se o SO for Ubuntu, e ajuste o caminho do .pem)"
echo "======================================================================"
