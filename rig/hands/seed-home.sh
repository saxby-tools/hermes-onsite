#!/bin/sh
# Seeds the hands user's home with a sample repo and a shell setup
# that reproduces the PATH gap: `handstool` is only on PATH after
# .zshrc runs, and a non-interactive SSH shell never reads .zshrc.

set -eu

HOME_DIR="/home/${HANDS_USER}"
TOOLS_DIR="${HOME_DIR}/.local/tools/bin"
REPO_DIR="${HOME_DIR}/Development/sample"

mkdir -p "${TOOLS_DIR}"

cat > "${TOOLS_DIR}/handstool" <<'TOOL'
#!/bin/sh
echo "handstool ok"
TOOL

chmod 0755 "${TOOLS_DIR}/handstool"

cat > "${HOME_DIR}/.zshrc" <<'ZSHRC'
# PATH is built here on purpose. See seed-home.sh.
export PATH="${HOME}/.local/tools/bin:${PATH}"
ZSHRC

mkdir -p "${REPO_DIR}"

cd "${REPO_DIR}"

git init --quiet --initial-branch=main

git config user.name "Rig"
git config user.email "rig@example.invalid"

cat > README.md <<'README'
# sample

A throwaway repo for the hermes-hands test rig.
README

cat > notes.txt <<'NOTES'
alpha
bravo
charlie
NOTES

git add --all
git commit --quiet --message "chore: seed sample repo"
