#!/usr/bin/env python3
"""
Diagnostic automatique du projet Telegram Bot sur le VPS
Lance depuis ton PC : python3 vps_diagnostic.py
"""

import paramiko
import getpass
import os
import sys

VPS_HOST = "45.92.218.184"
VPS_PORT = 22
VPS_USER = "root"


def cmd(client, command):
    _, out, err = client.exec_command(command)
    return out.read().decode().strip(), err.read().decode().strip()


def section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


def connect():
    password = os.environ.get("VPS_PASSWORD") or getpass.getpass(
        f"Mot de passe root@{VPS_HOST}: "
    )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER,
                       password=password, timeout=10)
        print(f"[+] Connecté à {VPS_HOST}")
        return client
    except paramiko.AuthenticationException:
        print("[-] Mauvais mot de passe.")
        sys.exit(1)
    except Exception as e:
        print(f"[-] Connexion échouée : {e}")
        sys.exit(1)


def find_project(client):
    """Trouve le dossier du projet Telegram bot."""
    section("Recherche du projet")
    out, _ = cmd(client, "find /root /home -maxdepth 5 -name '*.py' 2>/dev/null")
    files = out.splitlines()
    print(f"Fichiers Python trouvés : {len(files)}")
    for f in files:
        print(f"  {f}")

    # Chercher les fichiers liés à telegram/mail
    out2, _ = cmd(client, "grep -rl 'telegram\\|telebot\\|smtplib\\|sendmail' /root /home 2>/dev/null")
    relevant = out2.splitlines()
    print(f"\nFichiers liés Telegram/Mail : {len(relevant)}")
    for f in relevant:
        print(f"  [!] {f}")

    return relevant


def check_syntax(client, files):
    """Vérifie la syntaxe Python de chaque fichier."""
    section("Vérification syntaxe Python")
    errors = []
    for f in files:
        out, err = cmd(client, f"python3 -m py_compile '{f}' 2>&1 && echo OK")
        if "OK" in out:
            print(f"  [OK] {f}")
        else:
            print(f"  [ERREUR] {f}")
            print(f"    -> {err or out}")
            errors.append((f, err or out))
    return errors


def check_dependencies(client, files):
    """Vérifie les imports et packages manquants."""
    section("Vérification des dépendances")
    # Extraire tous les imports
    imports_cmd = "grep -h '^import\\|^from' " + " ".join(f"'{f}'" for f in files) + " 2>/dev/null | sort -u"
    out, _ = cmd(client, imports_cmd)
    print("Imports détectés :")
    for line in out.splitlines():
        print(f"  {line}")

    # Vérifier packages installés
    print("\nPackages Python installés (pip) :")
    out2, _ = cmd(client, "pip3 list 2>/dev/null | grep -iE 'telegram|telebot|smtp|requests|flask|dotenv'")
    print(out2 if out2 else "  Aucun package Telegram/Mail trouvé !")

    # Vérifier pip3
    out3, _ = cmd(client, "pip3 list 2>/dev/null | wc -l")
    print(f"\nTotal packages installés : {out3}")


def check_running(client):
    """Vérifie si le bot tourne déjà."""
    section("Processus en cours")
    out, _ = cmd(client, "ps aux | grep -iE 'python|bot|telegram' | grep -v grep")
    if out:
        print("Processus actifs :")
        print(out)
    else:
        print("Aucun processus Python/bot en cours d'exécution.")

    # Vérifier services systemd
    out2, _ = cmd(client, "systemctl list-units --type=service --state=running 2>/dev/null | grep -iE 'bot|telegram|mail'")
    if out2:
        print(f"\nServices systemd actifs :\n{out2}")


def check_logs(client, files):
    """Cherche les fichiers de logs et affiche les dernières erreurs."""
    section("Logs et erreurs récentes")
    out, _ = cmd(client, "find /root /home /var/log -name '*.log' -newer /tmp 2>/dev/null | head -10")
    if out:
        print("Fichiers log trouvés :")
        for log in out.splitlines():
            print(f"  {log}")
            tail, _ = cmd(client, f"tail -20 '{log}' 2>/dev/null")
            if tail:
                print(f"  --- Dernières lignes ---")
                print(tail)

    # Chercher les .env / config
    section("Fichiers de configuration (.env / config)")
    out2, _ = cmd(client, "find /root /home -name '.env' -o -name 'config.py' -o -name 'config.json' 2>/dev/null")
    for f in out2.splitlines():
        print(f"  [CONFIG] {f}")
        content, _ = cmd(client, f"cat '{f}' 2>/dev/null")
        # Masquer les tokens/passwords
        for line in content.splitlines():
            key = line.split('=')[0] if '=' in line else line
            print(f"    {key}=***")


def show_file_content(client, files):
    """Affiche le contenu des fichiers principaux du projet."""
    section("Contenu des fichiers du projet")
    for f in files[:5]:  # Max 5 fichiers
        print(f"\n--- {f} ---")
        content, _ = cmd(client, f"cat '{f}'")
        print(content)


def main():
    client = connect()

    try:
        # 1. Trouver le projet
        relevant_files = find_project(client)

        if not relevant_files:
            print("\n[!] Projet Telegram/Mail introuvable.")
            print("    Cherche tous les .py dans /root...")
            out, _ = cmd(client, "find /root -name '*.py' 2>/dev/null")
            relevant_files = out.splitlines()

        # 2. Vérifier la syntaxe
        if relevant_files:
            syntax_errors = check_syntax(client, relevant_files)

        # 3. Vérifier les dépendances
        if relevant_files:
            check_dependencies(client, relevant_files)

        # 4. Processus actifs
        check_running(client)

        # 5. Logs
        check_logs(client, relevant_files)

        # 6. Afficher le code source
        if relevant_files:
            show_file_content(client, relevant_files)

        # Résumé
        section("RÉSUMÉ")
        if relevant_files and syntax_errors:
            print(f"[!] {len(syntax_errors)} erreur(s) de syntaxe trouvée(s) :")
            for f, e in syntax_errors:
                print(f"    {f}: {e}")
        elif relevant_files:
            print("[+] Aucune erreur de syntaxe détectée.")
        else:
            print("[!] Projet introuvable sur le VPS.")

    finally:
        client.close()
        print("\n[+] Connexion fermée.")


if __name__ == "__main__":
    main()
