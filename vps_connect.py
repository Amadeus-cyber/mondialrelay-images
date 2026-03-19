#!/usr/bin/env python3
"""
Script Paramiko - Connexion VPS
SSH + SFTP : exécution de commandes et transfert de fichiers
"""

import paramiko
import getpass
import os
import sys


VPS_HOST = "45.92.218.184"
VPS_PORT = 22
VPS_USER = "root"


def connect(password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=VPS_HOST,
        port=VPS_PORT,
        username=VPS_USER,
        password=password,
        timeout=10,
    )
    print(f"[+] Connecté à {VPS_HOST} en tant que {VPS_USER}")
    return client


def run_command(client: paramiko.SSHClient, command: str) -> str:
    """Exécute une commande sur le VPS et retourne la sortie."""
    stdin, stdout, stderr = client.exec_command(command)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err:
        print(f"[STDERR] {err}")
    return out


def upload_file(client: paramiko.SSHClient, local_path: str, remote_path: str):
    """Upload un fichier local vers le VPS."""
    sftp = client.open_sftp()
    sftp.put(local_path, remote_path)
    sftp.close()
    print(f"[+] Upload : {local_path} -> {remote_path}")


def download_file(client: paramiko.SSHClient, remote_path: str, local_path: str):
    """Télécharge un fichier depuis le VPS."""
    sftp = client.open_sftp()
    sftp.get(remote_path, local_path)
    sftp.close()
    print(f"[+] Download : {remote_path} -> {local_path}")


def interactive_shell(client: paramiko.SSHClient):
    """Mini shell interactif pour exécuter des commandes."""
    print("\n[Shell interactif] Tape 'exit' pour quitter.\n")
    while True:
        cmd = input(f"{VPS_USER}@{VPS_HOST}:~$ ").strip()
        if cmd.lower() in ("exit", "quit"):
            break
        if not cmd:
            continue
        output = run_command(client, cmd)
        if output:
            print(output)


def main():
    # Mot de passe via variable d'env ou prompt sécurisé
    password = os.environ.get("VPS_PASSWORD") or getpass.getpass(
        f"Mot de passe pour {VPS_USER}@{VPS_HOST}: "
    )

    try:
        client = connect(password)
    except paramiko.AuthenticationException:
        print("[-] Échec d'authentification — mauvais mot de passe.")
        sys.exit(1)
    except Exception as e:
        print(f"[-] Erreur de connexion : {e}")
        sys.exit(1)

    try:
        # --- Exemples d'utilisation ---

        # 1. Exécuter une commande
        result = run_command(client, "uname -a && uptime")
        print(f"\n[Info système]\n{result}\n")

        # 2. Lister les fichiers d'un dossier
        files = run_command(client, "ls -la /root/")
        print(f"[Fichiers /root/]\n{files}\n")

        # 3. Upload (décommente et adapte le chemin)
        # upload_file(client, "/chemin/local/fichier.py", "/root/fichier.py")

        # 4. Download (décommente et adapte le chemin)
        # download_file(client, "/root/fichier.py", "/chemin/local/fichier.py")

        # 5. Shell interactif
        interactive_shell(client)

    finally:
        client.close()
        print("\n[+] Connexion fermée.")


if __name__ == "__main__":
    main()
