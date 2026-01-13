from pypdf import PdfReader
from datetime import date
import locale
import re
import uuid
import sqlalchemy
from sqlalchemy import select, exists
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship
from sqlalchemy.dialects.postgresql import UUID
import difflib
import os
from pathlib import Path
import sys

class Base(DeclarativeBase):
    pass

class Councilour(Base):
    __tablename__ = "councilours"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sqlalchemy.String, nullable=False)
    phone: Mapped[str] = mapped_column(sqlalchemy.String, nullable=True)
    email: Mapped[str] = mapped_column(sqlalchemy.String, nullable=False)
    photo_url: Mapped[str] = mapped_column(sqlalchemy.String, nullable=False)
    party: Mapped[str] = mapped_column(sqlalchemy.String, nullable=False)

    attendances: Mapped[list["Attendence"]] = relationship(back_populates="councilour")

class Attendence(Base):
    __tablename__ = "attendences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    councilor_id: Mapped[uuid.UUID] = mapped_column(sqlalchemy.ForeignKey("councilours.id"), nullable=False)
    month: Mapped[date] = mapped_column(sqlalchemy.Date, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)

    councilour: Mapped["Councilour"] = relationship(back_populates="attendances")

def get_councilour_name(name: str, councilours: list):
    names = [c.name for c in councilours]
    match = difflib.get_close_matches(name, names, n=1, cutoff=0.7)
    if match:
        return next(c for c in councilours if c.name == match[0])
    return None

def get_attendence_status_from_scrapped_str(text: str):
    return re.search(r"\b(PRESENTE|Ausente|Justificado)\b", text, re.IGNORECASE).group()

def get_name_from_scrapped_str(text: str):
    return re.sub(r"\b(PRESENTE|Ausente|Justificado)\b", "", text, re.IGNORECASE).strip()

def parse_date_from_string(date_str: str) -> date:
    """
    Converte uma string no formato "02 de Janeiro de 2024" para um objeto date.
    """
    match = re.match(r"^(\d{1,2}) de ([A-Za-zÀ-ÿ]+) de (\d{4})(?:\s*\|.*)?$", date_str)
    if not match:
        raise ValueError(f"Formato de data inválido: {date_str}")
    
    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3))
    
    months = {
        'janeiro': 1, 'fevereiro': 2, 'março': 3, 'abril': 4,
        'maio': 5, 'junho': 6, 'julho': 7, 'agosto': 8,
        'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12
    }
    
    month = months.get(month_name)
    if not month:
        raise ValueError(f"Nome do mês inválido: {month_name}")
    
    return date(year, month, day)

def add_attendence(client: sqlalchemy.Engine, attendences: list[Attendence], text: list[str]):
    session_date_regex = r'^\d{1,2} de [A-Za-zÀ-ÿ]+ de \d{4} \| [A-Za-zÀ-ÿ\s]+$'
    session_date_str: str = None
    session_date: date = None

    councilours = get_all_councilours(client)

    for i in text:
        if re.match(session_date_regex, i): # will always hit this condition on the first iteration
            session_date_str = i
            session_date = parse_date_from_string(i)
            continue
        if any(x in i for x in ['PRESENTE', 'Ausente', 'Justificado']):
            councilour = get_councilour_name(get_name_from_scrapped_str(i), councilours)

            if (councilour is None):
                print(f'O vereador {get_name_from_scrapped_str(i)} participou da reunião de {session_date_str}, mas ele não foi encontrado '
                      'na base de dados do paecirco.org.')
                continue

            attendences.append(Attendence(
                month=session_date,
                status=get_attendence_status_from_scrapped_str(i),
                councilor_id=councilour.id
            ))

def get_all_councilours(client: sqlalchemy.Engine):
    with Session(client) as session:
        stmt = sqlalchemy.select(Councilour)
        return session.scalars(stmt).all()

def throw_exception_if_current_month_already_executed(client: sqlalchemy.Engine, check_date: date):
    with Session(client) as session:
        stmt = select(Attendence).where(Attendence.month == check_date)

        has_any = session.scalars(stmt).all()

        if has_any:
            print('Parece que o mês atual já foi executado na base. Programa encerrado.')
            sys.exit(0)

def get_councilour_by_name(client: sqlalchemy.Engine, name: str):
    with Session(client) as session:
            stmt = sqlalchemy.select(Councilour).where(Councilour.name == name)
            return session.scalars(stmt).first()

def get_last_attendence_pdf_full_path():
    path = os.getenv("paoecirco.org_attendences_folder")

    attendences_files = [f for f in Path(path).glob("*.pdf") if f.stem.isdigit()]

    if attendences_files:
        latest_attendence_pdf = max(attendences_files, key=lambda f: int(f.stem))
        return latest_attendence_pdf
    else:
        print(f"Nenhum arquivo PDF encontrado em {path}. Os arquivos de presença precisam ser inseridos nessa pasta.")
        raise Exception()

def is_attendences_empty(client: sqlalchemy.Engine) -> bool:
    with Session(client) as session:
        stmt = select(Attendence)
        return len(session.scalars(stmt).all()) == 0

def get_all_attendence_pdfs_sorted():
    path = os.getenv("paoecirco.org_attendences_folder")
    attendences_files = [f for f in Path(path).glob("*.pdf") if f.stem.isdigit()]
    
    if not attendences_files:
        print(f"Nenhum arquivo PDF encontrado em {path}. Os arquivos de presença precisam ser inseridos nessa pasta.")
        raise Exception()
    
    return sorted(attendences_files, key=lambda f: int(f.stem))

def process_initial_migration(client: sqlalchemy.Engine):
    print("Parece que não há nenhuma presença de sessão armazenada na base de dados.")
    print("Pressione qualquer tecla para seguir com a migração inicial.")
    input()
    
    pdf_files = get_all_attendence_pdfs_sorted()
    
    for pdf_path in pdf_files:
        print(f"\nProcessando arquivo: {pdf_path.name}")
        reader = PdfReader(pdf_path)
        attendences = []
        
        for i in range(len(reader.pages)):
            page = reader.pages[i]
            text = page.extract_text().splitlines()
            add_attendence(client, attendences, text)
        
        with Session(client) as session:
            session.add_all(attendences)
            session.commit()
            print(f"Arquivo {pdf_path.name} processado e inserido com sucesso.")
    
    print("\nMigração inicial concluída com sucesso!")
    sys.exit(0)

locale.setlocale(locale.LC_TIME, 'pt_BR.utf8')

client = sqlalchemy.create_engine(
    "postgresql+psycopg2://postgres:postgres@server-database-1:5432/paoecirco.org",
    echo=False
)

Base.metadata.create_all(client)

if is_attendences_empty(client):
    process_initial_migration(client)

today = date.today()
throw_exception_if_current_month_already_executed(client, today)

path = get_last_attendence_pdf_full_path()
last_month = f"{today.year}/{today.month - 1}/{today.day}" 

print(f"O arquivo {path} será processado, ele deve representar o mês {last_month}. Se isso estiver correto, clique qualquer tecla para continuar.")
input()

print(f"\nIniciando a raspagem do relatório de presenças em {last_month}.")

reader = PdfReader(path)
page = reader.pages[0]
text = page.extract_text().splitlines()

attendences = []

for i in range(len(reader.pages)):
    page = reader.pages[i]
    text = page.extract_text().splitlines()
    add_attendence(client, attendences, text)

try:
    with Session(client) as session:
        print('Iniciando inserção das presenças/ausências das reuniões.')
        session.add_all(attendences)
        session.commit()
        print('Inserção das presenças/ausências das reuniões concluída.')
finally:
    pass
