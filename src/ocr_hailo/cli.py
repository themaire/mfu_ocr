from __future__ import annotations

import json
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
import typer

from .diagnostics import run_checks
from .extraction import extract_digital_text, process_pdf, write_text_output
from .metadata import extract_document_metadata, write_metadata_json

app = typer.Typer(help="Outils initiaux pour l'étude OCR sur Raspberry Pi + Hailo")
console = Console()


@app.command("check-env")
def check_env() -> None:
    """Affiche l'état du socle système utile au projet."""
    table = Table(title="Diagnostic du poste OCR")
    table.add_column("Composant")
    table.add_column("État")
    table.add_column("Détail")

    for item in run_checks():
        table.add_row(item.name, "OK" if item.ok else "Manquant", item.details)

    console.print(table)


@app.command("extract-text")
def extract_text(
    pdf_path: Path = typer.Argument(..., help="Chemin vers un PDF contenant du texte numérique"),
    output_path: Path = typer.Option(Path("output/resultat.txt"), "--output", "-o", help="Fichier texte de sortie"),
) -> None:
    """Extrait le texte natif d'un PDF et l'écrit dans un fichier."""
    text = extract_digital_text(pdf_path)
    if not text:
        console.print("[yellow]Aucun texte numérique détecté. Le document est probablement scanné.[/yellow]")
        raise typer.Exit(code=1)

    target = write_text_output(text, output_path)
    console.print(f"[green]Texte exporté dans :[/green] {target}")


@app.command("process-pdf")
def process_pdf_command(
    pdf_path: Path = typer.Argument(..., help="Chemin vers un PDF à traiter"),
    output_path: Path = typer.Option(Path("output/resultat.txt"), "--output", "-o", help="Fichier texte de sortie"),
    language: str = typer.Option("fra", "--lang", "-l", help="Langue Tesseract à utiliser"),
    json_output_path: Path | None = typer.Option(None, "--json-output", help="Fichier JSON de synthèse"),
    hailo: bool | None = typer.Option(None, "--hailo/--no-hailo", help="Activer/désactiver la détection de texte via Hailo NPU (auto par défaut)"),
    debug: bool = typer.Option(False, "--debug", help="Sauvegarder les crops Hailo dans input/crops/{pdf}/"),
) -> None:
    """Traite un PDF hybride : texte natif si possible, OCR sinon."""
    t0 = time.monotonic()
    text, analysis_text = process_pdf(pdf_path, language=language, return_analysis=True, use_hailo=hailo, debug=debug)
    if not text:
        console.print("[red]Aucun texte n'a pu être extrait du document.[/red]")
        raise typer.Exit(code=1)

    target = write_text_output(text, output_path)
    metadata = extract_document_metadata(analysis_text, pdf_path.name)
    json_target = write_metadata_json(metadata, json_output_path or output_path.with_suffix(".json"))

    # Insérer le JSON au début du fichier texte
    json_block = json.dumps(metadata, indent=2, ensure_ascii=False)
    separator = "\n" + "=" * 72 + "\n\n"
    original_text = target.read_text(encoding="utf-8")
    target.write_text(json_block + separator + original_text, encoding="utf-8")

    elapsed = time.monotonic() - t0
    minutes, seconds = divmod(elapsed, 60)
    console.print(f"[green]Traitement terminé en {int(minutes)}m{seconds:05.2f}s. Sortie texte :[/green] {target}")
    console.print(f"[green]Synthèse JSON :[/green] {json_target}")


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Adresse d'écoute"),
    port: int = typer.Option(5000, "--port", "-p", help="Port d'écoute"),
    debug: bool = typer.Option(False, "--debug", help="Mode debug Flask"),
) -> None:
    """Démarre le serveur API Flask."""
    from .api import app as flask_app
    console.print(f"[green]Serveur API démarré sur http://{host}:{port}[/green]")
    flask_app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    app()
