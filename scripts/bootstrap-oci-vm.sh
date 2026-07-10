#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "warning: expected aarch64, found $(uname -m)" >&2
fi

MEMORY_KB="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
CPU_COUNT="$(nproc)"
if (( MEMORY_KB < 7_500_000 || CPU_COUNT < 2 )); then
  echo "minimum requirement is 2 CPU and 8 GB RAM" >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl gpg jq python3-venv rsync unzip
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
# shellcheck disable=SC1091
source /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" |
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "${USER}"

if ! command -v oci >/dev/null 2>&1; then
  sudo python3 -m venv /opt/oci-cli
  sudo /opt/oci-cli/bin/pip install --disable-pip-version-check "oci-cli==3.89.1"
  sudo ln -s /opt/oci-cli/bin/oci /usr/local/bin/oci
fi

sudo install -d -o "${USER}" -g "${USER}" /opt/quant-trend-lab/releases
sudo install -d -o "${USER}" -g "${USER}" /srv/quant-trend-lab
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "20m",
    "max-file": "5"
  }
}
EOF
sudo systemctl enable --now docker
sudo systemctl restart docker

echo "VM baseline complete. Log out and back in before using Docker without sudo."
