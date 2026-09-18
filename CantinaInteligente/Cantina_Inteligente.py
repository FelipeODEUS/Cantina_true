import sys
import os
import json
import time
import random
import threading
import logging
from datetime import datetime, timedelta
import cv2
import numpy as np
import requests

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QGraphicsDropShadowEffect, QProgressBar, QDialog, QDoubleSpinBox,
    QSpinBox, QLineEdit, QComboBox
)
from PyQt6.QtCore import QTimer, Qt, QThread, pyqtSignal, QSize, QRectF
from PyQt6.QtGui import QImage, QPixmap, QColor, QIcon, QPainter, QBrush, QPen, QFont, QLinearGradient

import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from ultralytics import YOLO

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PASTA_RAIZ = os.path.dirname(os.path.abspath(__file__))
PASTA_FACES = os.path.join(PASTA_RAIZ, "faces_cadastradas")
ARQUIVO_HISTORICO = os.path.join(PASTA_RAIZ, "historico_cantina.json")
ARQUIVO_CONFIG = os.path.join(PASTA_RAIZ, "config_financeiro.json")
ARQUIVO_ALUNOS = os.path.join(PASTA_RAIZ, "alunos_escola.json")
PASTA_ASSETS = os.path.join(PASTA_RAIZ, "assets")

os.makedirs(PASTA_FACES, exist_ok=True)
os.makedirs(PASTA_ASSETS, exist_ok=True)

LOCK_BANCO = threading.RLock()
LOCK_ALUNOS = threading.RLock()
CACHE_DADOS = []

# ===========================================================================
# CONFIGURAÇÃO DA API DO TELEGRAM
# ===========================================================================
TELEGRAM_BOT_TOKEN = "8661207189:AAH2BnZq16IAOOJ9nEjTfXSAGRXu3TDCXIg"

CONFIG_FINANCEIRO_PADRAO = {
    "precos": {
        "in_natura": 4.50,
        "assados": 8.00,
        "frituras": 9.00,
        "doces": 5.00,
        "bebidas_processadas": 6.50
    },
    "custos_pct": {
        "in_natura": 40.0,
        "assados": 45.0,
        "frituras": 50.0,
        "doces": 35.0,
        "bebidas_processadas": 35.0
    }
}
CONFIG_FINANCEIRO = dict(CONFIG_FINANCEIRO_PADRAO)

ROTULOS_CATEGORIAS = {
    "in_natura": "In Natura / Frutas",
    "assados": "Salgados Assados",
    "frituras": "Frituras & Crocantes",
    "doces": "Doces & Sobremesas",
    "bebidas_processadas": "Bebidas / Sucos"
}

MAPEAMENTO_TEXTURA = {
    46: ("in_natura", (0, 230, 160), "[IN NATURA]"),
    47: ("in_natura", (0, 230, 160), "[IN NATURA]"),
    49: ("in_natura", (0, 230, 160), "[IN NATURA]"),
    50: ("in_natura", (0, 230, 160), "[IN NATURA]"),
    51: ("in_natura", (0, 230, 160), "[IN NATURA]"),
    48: ("assados", (255, 120, 20), "[ASSADO]"),
    53: ("assados", (255, 120, 20), "[ASSADO]"),
    52: ("frituras", (120, 100, 255), "[FRITURA]"),
    54: ("frituras", (120, 100, 255), "[FRITURA]"),
    55: ("doces", (255, 105, 160), "[DOCE]"),
    39: ("bebidas_processadas", (70, 185, 255), "[BEBIDA]"),
    41: ("bebidas_processadas", (70, 185, 255), "[BEBIDA]")
}

COR_BG = "#020617"
COR_CARD = "#090F20"
COR_CARD_HEADER = "#0E172F"
COR_BORDA = "#1A2B4C"
COR_AZUL = "#0066FF"
COR_AZUL_CLARO = "#38BDF8"
COR_TEXTO = "#F8FAFC"
COR_TEXTO_SEC = "#94A3B8"
COR_VERDE = "#10B981"
COR_VERMELHO = "#F43F5E"
COR_ROXO = "#A855F7"
COR_LARANJA = "#F97316"
COR_AMARELO = "#FBBF24"

FONTE = "'Segoe UI', 'Inter', -apple-system, BlinkMacSystemFont, Arial, sans-serif"


def carregar_configuracao_financeira():
    global CONFIG_FINANCEIRO
    if os.path.exists(ARQUIVO_CONFIG):
        try:
            with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
                CONFIG_FINANCEIRO = json.load(f)
                return
        except Exception:
            pass
    CONFIG_FINANCEIRO = dict(CONFIG_FINANCEIRO_PADRAO)


def salvar_configuracao_financeira():
    try:
        with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
            json.dump(CONFIG_FINANCEIRO, f, indent=4)
    except Exception as e:
        logging.error(f"Erro config financeira: {e}")


def inicializar_banco():
    with LOCK_BANCO:
        if not os.path.exists(ARQUIVO_HISTORICO):
            with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
                json.dump({"registros": []}, f, indent=4)
    with LOCK_ALUNOS:
        if not os.path.exists(ARQUIVO_ALUNOS):
            with open(ARQUIVO_ALUNOS, "w", encoding="utf-8") as f:
                json.dump({"alunos": []}, f, indent=4)


def carregar_alunos():
    with LOCK_ALUNOS:
        if os.path.exists(ARQUIVO_ALUNOS):
            try:
                with open(ARQUIVO_ALUNOS, "r", encoding="utf-8") as f:
                    return json.load(f).get("alunos", [])
            except Exception:
                return []
        return []


def salvar_novo_aluno(novo_aluno):
    with LOCK_ALUNOS:
        alunos = carregar_alunos()
        alunos.append(novo_aluno)
        try:
            with open(ARQUIVO_ALUNOS, "w", encoding="utf-8") as f:
                json.dump({"alunos": alunos}, f, indent=4)
            return True
        except Exception as e:
            logging.error(f"Erro ao salvar aluno: {e}")
            return False


def excluir_aluno_por_id(aluno_id):
    with LOCK_ALUNOS:
        alunos = carregar_alunos()
        novos_alunos = []
        for al in alunos:
            if al.get("id") == aluno_id:
                foto = al.get("foto")
                if foto and os.path.exists(foto):
                    try:
                        os.remove(foto)
                    except Exception:
                        pass
            else:
                novos_alunos.append(al)
        try:
            with open(ARQUIVO_ALUNOS, "w", encoding="utf-8") as f:
                json.dump({"alunos": novos_alunos}, f, indent=4)
            return True
        except Exception as e:
            logging.error(f"Erro ao excluir aluno: {e}")
            return False


def limpar_todos_alunos():
    with LOCK_ALUNOS:
        alunos = carregar_alunos()
        for al in alunos:
            foto = al.get("foto")
            if foto and os.path.exists(foto):
                try:
                    os.remove(foto)
                except Exception:
                    pass
        try:
            with open(ARQUIVO_ALUNOS, "w", encoding="utf-8") as f:
                json.dump({"alunos": []}, f, indent=4)
            return True
        except Exception as e:
            logging.error(f"Erro ao limpar alunos: {e}")
            return False


def carregar_dados():
    global CACHE_DADOS
    with LOCK_BANCO:
        if not os.path.exists(ARQUIVO_HISTORICO):
            return []
        try:
            with open(ARQUIVO_HISTORICO, "r", encoding="utf-8") as f:
                CACHE_DADOS = json.load(f).get("registros", [])
                return list(CACHE_DADOS)
        except Exception:
            return list(CACHE_DADOS)


def registrar_compra(in_natura, assados, frituras, doces, bebidas, ciclo, aluno_info):
    if (in_natura + assados + frituras + doces + bebidas) <= 0:
        return
    p = CONFIG_FINANCEIRO["precos"]
    faturamento = (
        in_natura * p["in_natura"] +
        assados * p["assados"] +
        frituras * p["frituras"] +
        doces * p["doces"] +
        bebidas * p["bebidas_processadas"]
    )
    reg = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": datetime.now().strftime("%Y-%m-%d"),
        "hora": datetime.now().strftime("%H:%M:%S"),
        "ciclo": ciclo,
        "aluno_id": aluno_info.get("id", "ALU-AVULSO") if aluno_info else "ALU-AVULSO",
        "aluno_nome": aluno_info.get("nome", "Não Identificado") if aluno_info else "Não Identificado",
        "in_natura": in_natura,
        "assados": assados,
        "frituras": frituras,
        "doces": doces,
        "bebidas_processadas": bebidas,
        "itens_saudaveis": in_natura + assados,
        "itens_processados": frituras + doces + bebidas,
        "faturamento_estimado": round(faturamento, 2)
    }
    with LOCK_BANCO:
        dados = []
        if os.path.exists(ARQUIVO_HISTORICO):
            with open(ARQUIVO_HISTORICO, "r", encoding="utf-8") as f:
                dados = json.load(f).get("registros", [])
        dados.append(reg)
        with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
            json.dump({"registros": dados}, f, indent=4)


def gerar_relatorio_aluno(aluno, dados_historico, dias_atras=7):
    data_limite = (datetime.now() - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
    registros_aluno = [d for d in dados_historico if d.get("aluno_id") == aluno["id"] and d.get("data", "") >= data_limite]

    total_natura = sum(d.get("in_natura", 0) for d in registros_aluno)
    total_assados = sum(d.get("assados", 0) for d in registros_aluno)
    total_frituras = sum(d.get("frituras", 0) for d in registros_aluno)
    total_doces = sum(d.get("doces", 0) for d in registros_aluno)
    total_bebidas = sum(d.get("bebidas_processadas", 0) for d in registros_aluno)

    total_saudaveis = total_natura + total_assados
    total_processados = total_frituras + total_doces + total_bebidas
    total_gasto = sum(d.get("faturamento_estimado", 0.0) for d in registros_aluno)
    teto = aluno.get("limite_semanal", 4)

    status_alerta = "LIMITE ULTRAPASSADO" if total_processados >= teto else "DENTRO DA META"

    mensagem = (
        f"🏫 RELATORIO NUTRICIONAL ESCOLAR — JPPN TECH\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Aluno: {aluno['nome']}\n"
        f"📚 Turma: {aluno.get('turma', 'N/A')}\n"
        f"📅 Periodo: Ultimos {dias_atras} dias\n"
        f"📊 Status: {status_alerta}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🥗 Itens Saudaveis: {total_saudaveis} un.\n"
        f"  • Frutas / In Natura: {total_natura} un.\n"
        f"  • Salgados Assados: {total_assados} un.\n\n"
        f"🍔 Ultraprocessados: {total_processados} / {teto} un. (Teto)\n"
        f"  • Frituras: {total_frituras} un.\n"
        f"  • Doces: {total_doces} un.\n"
        f"  • Bebidas: {total_bebidas} un.\n\n"
        f"💰 Gasto no Periodo: R$ {total_gasto:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Emitido pelo AI Box Auditor da Cantina."
    )
    return mensagem


def enviar_mensagem_telegram_sincrona(bot_token, chat_id, mensagem):
    if not bot_token or "SEU_TELEGRAM" in bot_token:
        return False, "Token nao configurado! Preencha a variavel TELEGRAM_BOT_TOKEN com o token do @BotFather."

    chat_id_limpo = str(chat_id).strip().replace("@", "")
    if not chat_id_limpo or not chat_id_limpo.lstrip("-").isdigit():
        return False, f"Chat ID '{chat_id}' invalido! Use o ID numerico obtido no @userinfobot."

    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = {
        "chat_id": chat_id_limpo,
        "text": mensagem
    }

    try:
        resp = requests.post(url, json=payload, timeout=8)
        dados = resp.json()

        if resp.status_code == 200 and dados.get("ok"):
            return True, "Mensagem enviada com sucesso!"
        else:
            erro_desc = dados.get("description", resp.text)
            if "chat not found" in erro_desc.lower():
                return False, f"Chat {chat_id_limpo} nao encontrado. O pai/responsavel deve abrir o bot e dar /start!"
            elif "bot was blocked" in erro_desc.lower():
                return False, "O usuario bloqueou o bot no Telegram."
            return False, f"Telegram recusou: {erro_desc}"
    except Exception as e:
        return False, f"Falha de rede com o Telegram: {e}"


def disparar_alerta_telegram(bot_token, chat_id, mensagem):
    threading.Thread(
        target=enviar_mensagem_telegram_sincrona,
        args=(bot_token, chat_id, mensagem),
        daemon=True
    ).start()


def calcular_score_nutricional(dados):
    if not dados:
        return 100
    tot_natura = sum(d.get("in_natura", 0) for d in dados)
    tot_assados = sum(d.get("assados", 0) for d in dados)
    tot_frituras = sum(d.get("frituras", 0) for d in dados)
    tot_doces = sum(d.get("doces", 0) for d in dados)
    tot_bebidas = sum(d.get("bebidas_processadas", 0) for d in dados)
    
    pontos_pos = (tot_natura * 2.0) + (tot_assados * 1.0)
    penalidades = (tot_frituras * 2.0) + (tot_doces * 1.5) + (tot_bebidas * 1.5)
    total_ponderado = pontos_pos + penalidades
    if total_ponderado == 0:
        return 100
    return int(max(0, min(100, (pontos_pos / total_ponderado) * 100)))


def carregar_ou_gerar_icone(nome_arquivo, sigla, cor_acento_hex, tamanho=50):
    extensoes = [".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG", ".svg"]
    pastas = [PASTA_ASSETS, PASTA_RAIZ]
    caminho = None
    for pasta in pastas:
        if not os.path.exists(pasta):
            continue
        for ext in extensoes:
            teste = os.path.join(pasta, f"{nome_arquivo}{ext}")
            if os.path.exists(teste) and os.path.getsize(teste) > 0:
                caminho = teste
                break
        if caminho:
            break

    base_pix = QPixmap(tamanho, tamanho)
    base_pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(base_pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    gradiente = QLinearGradient(0, 0, 0, tamanho)
    gradiente.setColorAt(0.0, QColor("#FFFFFF"))
    gradiente.setColorAt(1.0, QColor("#F1F5F9"))

    cor_borda = QColor(cor_acento_hex)
    painter.setBrush(QBrush(gradiente))
    painter.setPen(QPen(cor_borda, 2.0))
    painter.drawRoundedRect(QRectF(1.5, 1.5, tamanho - 3, tamanho - 3), 14, 14)

    if caminho:
        pix_orig = QPixmap(caminho)
        if not pix_orig.isNull():
            margem = int(tamanho * 0.16)
            interno = tamanho - (margem * 2)
            pix_redim = pix_orig.scaled(interno, interno, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((tamanho - pix_redim.width()) // 2, (tamanho - pix_redim.height()) // 2, pix_redim)
            painter.end()
            return QIcon(base_pix)

    painter.setPen(QColor("#0F172A"))
    painter.setFont(QFont("Segoe UI", 10 if len(sigla) <= 3 else 8, QFont.Weight.Black))
    painter.drawText(QRectF(0, 0, tamanho, tamanho), Qt.AlignmentFlag.AlignCenter, sigla)
    painter.end()
    return QIcon(base_pix)


def extrair_caracteristicas_face(img_face):
    if img_face is None or img_face.size == 0:
        return None
    try:
        cinza = cv2.cvtColor(img_face, cv2.COLOR_BGR2GRAY)
        cinza = cv2.resize(cinza, (80, 80))
        hist = cv2.calcHist([cinza], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        return hist
    except Exception:
        return None


# ===========================================================================
# THREAD MESTRE DE CÂMERAS
# ===========================================================================
class CameraMasterThread(QThread):
    frame_cam0 = pyqtSignal(np.ndarray)
    frame_cam1 = pyqtSignal(np.ndarray)

    def __init__(self):
        super().__init__()
        self.rodando = True

    def run(self):
        cap0 = cv2.VideoCapture(0)
        if not cap0.isOpened() and sys.platform.startswith("win"):
            cap0 = cv2.VideoCapture(0, cv2.CAP_DSHOW)

        cap1 = cv2.VideoCapture(1)
        if not cap1.isOpened() and sys.platform.startswith("win"):
            cap1 = cv2.VideoCapture(1, cv2.CAP_DSHOW)

        tem_cam1 = cap1.isOpened()

        while self.rodando:
            if cap0.isOpened():
                ret0, f0 = cap0.read()
                if ret0 and f0 is not None:
                    self.frame_cam0.emit(f0)
                    if not tem_cam1:
                        self.frame_cam1.emit(f0)

            if tem_cam1 and cap1.isOpened():
                ret1, f1 = cap1.read()
                if ret1 and f1 is not None:
                    self.frame_cam1.emit(f1)

            time.sleep(0.03)

        if cap0.isOpened():
            cap0.release()
        if cap1 and cap1.isOpened():
            cap1.release()

    def parar(self):
        self.rodando = False
        self.wait(800)


# ===========================================================================
# WORKERS DE PROCESSAMENTO
# ===========================================================================
class WorkerIAAlimentos(QThread):
    frame_processado = pyqtSignal(np.ndarray, dict)
    evento_compra = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.rodando = True
        self.ultimo_frame = None
        self.lock = threading.Lock()
        self.alimento_em_cena = False
        self.tempo_ultimo_objeto = 0
        
        caminho_best = os.path.join(PASTA_RAIZ, "best.pt")
        modelo_path = caminho_best if os.path.exists(caminho_best) else "yolov8n.pt"
        try:
            self.modelo = YOLO(modelo_path)
        except Exception:
            self.modelo = None

    def receber_frame(self, frame):
        with self.lock:
            self.ultimo_frame = frame.copy()

    def run(self):
        while self.rodando:
            frame = None
            with self.lock:
                if self.ultimo_frame is not None:
                    frame = self.ultimo_frame
                    self.ultimo_frame = None

            if frame is None:
                time.sleep(0.02)
                continue

            frame = cv2.resize(frame, (480, 320))
            contagens = {"in_natura": 0, "assados": 0, "frituras": 0, "doces": 0, "bebidas_processadas": 0}

            if self.modelo:
                try:
                    res = self.modelo(frame, conf=0.25, verbose=False)
                    for r in res:
                        for box in r.boxes:
                            cid = int(box.cls[0])
                            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                            if cid in MAPEAMENTO_TEXTURA:
                                cat, cor, tag = MAPEAMENTO_TEXTURA[cid]
                                contagens[cat] += 1
                                cor_bgr = (cor[2], cor[1], cor[0])
                                cv2.rectangle(frame, (x1, y1), (x2, y2), cor_bgr, 2)
                                cv2.putText(frame, tag, (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.40, cor_bgr, 2)
                except Exception:
                    pass

            total = sum(contagens.values())
            agora = time.time()
            if total > 0:
                self.tempo_ultimo_objeto = agora
                if not self.alimento_em_cena:
                    self.evento_compra.emit(contagens)
                    self.alimento_em_cena = True
            else:
                if self.alimento_em_cena and (agora - self.tempo_ultimo_objeto > 1.5):
                    self.alimento_em_cena = False

            status_cor = (0, 255, 255) if self.alimento_em_cena else (16, 185, 129)
            status_txt = "● BALCAO OCUPADO | AUDITADO" if self.alimento_em_cena else "● CAM 1: BALCAO LIVRE"
            cv2.putText(frame, status_txt, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.50, status_cor, 2)

            self.frame_processado.emit(frame, contagens)
            time.sleep(0.03)

    def parar(self):
        self.rodando = False
        self.wait(600)


class WorkerIAFacial(QThread):
    frame_processado = pyqtSignal(np.ndarray, object)

    def __init__(self):
        super().__init__()
        self.rodando = True
        self.ultimo_frame = None
        self.lock = threading.Lock()
        self.alunos = []
        self.alunos_features = {}
        self.carregar_base_faces()

        try:
            self.modelo_face = YOLO("yolov8n.pt")
        except Exception:
            self.modelo_face = None

    def carregar_base_faces(self):
        self.alunos = carregar_alunos()
        self.alunos_features = {}
        for al in self.alunos:
            foto_path = al.get("foto")
            if foto_path and os.path.exists(foto_path):
                img = cv2.imread(foto_path)
                feat = extrair_caracteristicas_face(img)
                if feat is not None:
                    self.alunos_features[al["id"]] = (al, feat)

    def atualizar_lista(self):
        self.carregar_base_faces()

    def receber_frame(self, frame):
        with self.lock:
            self.ultimo_frame = frame.copy()

    def run(self):
        while self.rodando:
            frame = None
            with self.lock:
                if self.ultimo_frame is not None:
                    frame = self.ultimo_frame
                    self.ultimo_frame = None

            if frame is None:
                time.sleep(0.02)
                continue

            frame = cv2.resize(frame, (480, 320))
            aluno_reconhecido = None
            face_detectada = False

            if self.modelo_face:
                try:
                    res = self.modelo_face(frame, classes=[0], conf=0.35, verbose=False)
                    for r in res:
                        for box in r.boxes:
                            face_detectada = True
                            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                            
                            h_pessoa = max(10, y2 - y1)
                            y2_face = min(frame.shape[0], y1 + int(h_pessoa * 0.40))
                            x1_c, y1_c = max(0, x1), max(0, y1)
                            x2_c = min(frame.shape[1], x2)
                            crop_face = frame[y1_c:y2_face, x1_c:x2_c]
                            
                            feat_atual = extrair_caracteristicas_face(crop_face)
                            melhor_score = -1.0
                            candidato = None

                            if feat_atual is not None and len(self.alunos_features) > 0:
                                for al_id, (dados_al, feat_ref) in self.alunos_features.items():
                                    score = cv2.compareHist(feat_atual, feat_ref, cv2.HISTCMP_CORREL)
                                    if score > melhor_score:
                                        melhor_score = score
                                        candidato = dados_al

                            if candidato is not None and melhor_score > 0.50:
                                aluno_reconhecido = candidato
                                nome_d = f"{candidato['nome']} ({int(melhor_score*100)}%)"
                                cor = (16, 185, 129)
                            else:
                                nome_d = "DESCONHECIDO / NAO CADASTRADO"
                                cor = (0, 102, 255)

                            cv2.rectangle(frame, (x1, y1), (x2, y2), cor, 2)
                            cv2.rectangle(frame, (x1_c, y1_c), (x2_c, y2_face), (56, 189, 248), 1)
                            cv2.putText(frame, nome_d, (x1, max(20, y1 - 8)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, cor, 2)
                except Exception:
                    pass

            if face_detectada:
                txt = f"● ALUNO: {aluno_reconhecido['nome']}" if aluno_reconhecido else "● ROSTO DETECTADO (SEM CADASTRO)"
                cv2.putText(frame, txt, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (56, 189, 248), 2)
            else:
                cv2.putText(frame, "● CAM 2: BUSCANDO ALUNO...", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148, 163, 184), 2)

            self.frame_processado.emit(frame, aluno_reconhecido)
            time.sleep(0.03)

    def parar(self):
        self.rodando = False
        self.wait(600)


# ===========================================================================
# MODAL DE CADASTRO COM FEED AO VIVO
# ===========================================================================
class ModalCadastroAlunoFacial(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cadastro de Aluno com Reconhecimento Facial")
        self.setFixedSize(680, 480)
        self.ultimo_frame_stream = None
        self.frame_congelado = None

        self.setStyleSheet(f"""
            QDialog {{ background-color: {COR_BG}; }}
            QLabel {{ color: {COR_TEXTO}; font-family: {FONTE}; font-size: 11px; }}
            QLineEdit, QComboBox, QSpinBox {{
                background-color: #0F1A30; color: #FFFFFF; border: 1px solid #233B6E;
                border-radius: 8px; padding: 6px; font-size: 12px;
            }}
            QPushButton#BtnSnap {{
                background-color: #0284C7; color: #FFFFFF; font-weight: bold; border-radius: 8px; padding: 10px;
            }}
            QPushButton#BtnSalvar {{
                background-color: #10B981; color: #022C22; font-weight: bold; border-radius: 8px; padding: 10px;
            }}
        """)
        self.init_ui()

    def init_ui(self):
        l = QHBoxLayout(self)
        l.setSpacing(14)

        col_cam = QVBoxLayout()
        lbl_c = QLabel("Enquadre o Aluno na Câmera")
        lbl_c.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {COR_AZUL_CLARO};")
        col_cam.addWidget(lbl_c)

        self.lbl_preview = QLabel()
        self.lbl_preview.setFixedSize(300, 240)
        self.lbl_preview.setStyleSheet("background-color: #000; border: 2px solid #1E3A8A; border-radius: 10px;")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_cam.addWidget(self.lbl_preview)

        self.btn_snap = QPushButton("📸 Capturar Foto do Rosto")
        self.btn_snap.setObjectName("BtnSnap")
        self.btn_snap.clicked.connect(self.capturar_foto)
        col_cam.addWidget(self.btn_snap)

        self.lbl_aviso = QLabel("Posicione o aluno e clique em Capturar.")
        self.lbl_aviso.setStyleSheet(f"color: {COR_AMARELO}; font-weight: bold;")
        col_cam.addWidget(self.lbl_aviso)
        col_cam.addStretch()
        l.addLayout(col_cam)

        col_form = QVBoxLayout()
        lbl_f = QLabel("Dados do Aluno e Responsável")
        lbl_f.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {COR_AZUL_CLARO};")
        col_form.addWidget(lbl_f)

        self.in_nome = QLineEdit()
        self.in_nome.setPlaceholderText("Ex: Pedro Henrique")
        col_form.addWidget(QLabel("Nome do Aluno:"))
        col_form.addWidget(self.in_nome)

        self.cb_turma = QComboBox()
        self.cb_turma.addItems(["Fundamental 1", "Fundamental 2", "Ensino Médio"])
        col_form.addWidget(QLabel("Turma / Ciclo:"))
        col_form.addWidget(self.cb_turma)

        self.in_resp = QLineEdit()
        self.in_resp.setPlaceholderText("Ex: Mariana Silva")
        col_form.addWidget(QLabel("Nome do Responsável:"))
        col_form.addWidget(self.in_resp)

        self.in_chat = QLineEdit()
        self.in_chat.setPlaceholderText("Ex: 123456789 (Obtido no @userinfobot)")
        col_form.addWidget(QLabel("Telegram Chat ID dos Pais:"))
        col_form.addWidget(self.in_chat)

        self.spin_limite = QSpinBox()
        self.spin_limite.setRange(1, 20)
        self.spin_limite.setValue(4)
        col_form.addWidget(QLabel("Teto Semanal de Ultraprocessados:"))
        col_form.addWidget(self.spin_limite)

        self.btn_salvar = QPushButton("✅ Concluir Cadastro")
        self.btn_salvar.setObjectName("BtnSalvar")
        self.btn_salvar.clicked.connect(self.salvar)
        col_form.addWidget(self.btn_salvar)
        l.addLayout(col_form)

    def receber_stream_ao_vivo(self, frame):
        self.ultimo_frame_stream = frame.copy()
        if self.frame_congelado is None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
            self.lbl_preview.setPixmap(QPixmap.fromImage(qimg).scaled(self.lbl_preview.size(), Qt.AspectRatioMode.KeepAspectRatio))

    def capturar_foto(self):
        if self.ultimo_frame_stream is not None:
            self.frame_congelado = self.ultimo_frame_stream.copy()
            rgb = cv2.cvtColor(self.frame_congelado, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
            self.lbl_preview.setPixmap(QPixmap.fromImage(qimg).scaled(self.lbl_preview.size(), Qt.AspectRatioMode.KeepAspectRatio))
            self.lbl_aviso.setText("✅ Foto capturada com sucesso!")
            self.lbl_aviso.setStyleSheet(f"color: {COR_VERDE}; font-weight: bold;")
        else:
            QMessageBox.warning(self, "Aviso", "Aguardando sinal da câmera...")

    def salvar(self):
        nome = self.in_nome.text().strip()
        resp = self.in_resp.text().strip()
        chat_id = self.in_chat.text().strip()

        if not nome or not resp:
            QMessageBox.warning(self, "Aviso", "Preencha o nome do aluno e o responsável.")
            return

        if self.frame_congelado is None and self.ultimo_frame_stream is not None:
            self.frame_congelado = self.ultimo_frame_stream.copy()

        if self.frame_congelado is None:
            QMessageBox.warning(self, "Aviso", "Capture a foto do aluno antes de salvar.")
            return

        alu_id = f"ALU-{random.randint(100, 999)}"
        caminho_foto = os.path.join(PASTA_FACES, f"{alu_id}.jpg")
        cv2.imwrite(caminho_foto, self.frame_congelado)

        novo = {
            "id": alu_id,
            "nome": nome,
            "turma": self.cb_turma.currentText(),
            "responsavel": resp,
            "telegram_chat_id": chat_id,
            "foto": caminho_foto,
            "limite_semanal": self.spin_limite.value()
        }

        sucesso = salvar_novo_aluno(novo)
        if sucesso:
            self.accept()
        else:
            QMessageBox.critical(self, "Erro", "Não foi possível gravar no arquivo.")


# ===========================================================================
# MODAL PREÇOS E CUSTOS
# ===========================================================================
class ModalConfiguracaoPrecos(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Precificação & Custos")
        self.setFixedSize(520, 380)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {COR_BG}; }}
            QLabel {{ color: {COR_TEXTO}; font-family: {FONTE}; font-size: 12px; }}
            QDoubleSpinBox {{
                background-color: #0F1A30; color: #FFFFFF; border: 1px solid #233B6E;
                border-radius: 8px; padding: 5px; font-size: 12px;
            }}
            QPushButton#BtnSalvar {{
                background-color: #10B981; color: #022C22; font-weight: bold; border-radius: 8px; padding: 8px 14px;
            }}
            QPushButton#BtnCancelar {{
                background-color: #1E293B; color: #FFFFFF; border: 1px solid #334155; border-radius: 8px; padding: 8px 14px;
            }}
        """)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        lbl = QLabel("Parâmetros Financeiros")
        lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {COR_AZUL_CLARO};")
        layout.addWidget(lbl)

        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Categoria</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Preço de Venda (R$)</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Custo Insumo (%)</b>"), 0, 2)

        self.inputs_precos = {}
        self.inputs_custos = {}
        categorias = ["in_natura", "assados", "frituras", "doces", "bebidas_processadas"]

        for idx, cat in enumerate(categorias, start=1):
            lbl_cat = QLabel(ROTULOS_CATEGORIAS[cat])
            spin_p = QDoubleSpinBox()
            spin_p.setPrefix("R$ ")
            spin_p.setRange(0.50, 100.00)
            spin_p.setValue(CONFIG_FINANCEIRO["precos"].get(cat, 5.00))

            spin_c = QDoubleSpinBox()
            spin_c.setSuffix("%")
            spin_c.setRange(5.0, 95.0)
            spin_c.setValue(CONFIG_FINANCEIRO["custos_pct"].get(cat, 40.0))

            grid.addWidget(lbl_cat, idx, 0)
            grid.addWidget(spin_p, idx, 1)
            grid.addWidget(spin_c, idx, 2)
            self.inputs_precos[cat] = spin_p
            self.inputs_custos[cat] = spin_c

        layout.addLayout(grid)
        layout.addStretch()

        botoes = QHBoxLayout()
        botoes.addStretch()
        btn_c = QPushButton("Cancelar")
        btn_c.setObjectName("BtnCancelar")
        btn_c.clicked.connect(self.reject)
        btn_s = QPushButton("Salvar")
        btn_s.setObjectName("BtnSalvar")
        btn_s.clicked.connect(self.salvar)
        botoes.addWidget(btn_c)
        botoes.addWidget(btn_s)
        layout.addLayout(botoes)

    def salvar(self):
        global CONFIG_FINANCEIRO
        for cat, spin in self.inputs_precos.items():
            CONFIG_FINANCEIRO["precos"][cat] = spin.value()
        for cat, spin in self.inputs_custos.items():
            CONFIG_FINANCEIRO["custos_pct"][cat] = spin.value()
        salvar_configuracao_financeira()
        self.accept()


# ===========================================================================
# CANVAS MATPLOTLIB
# ===========================================================================
class GraficoModernoCanvas(FigureCanvas):
    def __init__(self, parent=None, width=4.5, height=3.2, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi, facecolor=COR_CARD)
        self.axes = self.fig.add_subplot(111)
        self.axes.set_facecolor(COR_CARD)
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        self.axes.spines['left'].set_color(COR_BORDA)
        self.axes.spines['bottom'].set_color(COR_BORDA)
        self.fig.tight_layout(pad=1.5)
        super().__init__(self.fig)
        self.setStyleSheet("background: transparent;")


def aplicar_sombra(widget, blur=30, cor=QColor(0, 102, 255, 45), y=0):
    efeito = QGraphicsDropShadowEffect(widget)
    efeito.setBlurRadius(blur)
    efeito.setColor(cor)
    efeito.setOffset(0, y)
    widget.setGraphicsEffect(efeito)


# ===========================================================================
# APLICAÇÃO PRINCIPAL
# ===========================================================================
class CantinaApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JPPN TECH — AI BOX AUDITOR")
        self.setMinimumSize(1380, 840)
        self.ciclo_selecionado = "Fundamental 1"
        self.aluno_em_foco = None
        self.modal_cadastro_aberto = None
        self.alertas_disparados = set()

        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {COR_BG}; }}
            QFrame#CardPrincipal {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0B1428, stop:1 #070D1B);
                border-radius: 16px; border: 1px solid {COR_BORDA};
            }}
            QFrame#CardHeader {{
                background-color: {COR_CARD_HEADER}; border-radius: 12px; border: 1px solid {COR_BORDA};
            }}
            QFrame#BarraNavSuperior {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #091226, stop:1 #0E1A38);
                border-radius: 16px; border: 1px solid #1E3A8A;
            }}
            QLabel {{ color: {COR_TEXTO}; font-family: {FONTE}; }}
            QPushButton {{
                background-color: transparent; color: {COR_TEXTO_SEC};
                border-radius: 10px; font-weight: 600; border: 1px solid transparent;
            }}
            QPushButton#IconeAbaGrande {{
                background: #FFFFFF;
                border-radius: 16px;
                border: 2px solid #E2E8F0;
                padding: 4px;
            }}
            QPushButton#IconeAbaGrande:hover {{
                background: #F8FAFC;
                border: 2px solid #38BDF8;
            }}
            QPushButton#IconeAbaGrandeAtiva {{
                background: #FFFFFF;
                border-radius: 16px;
                border: 3px solid #0066FF;
                padding: 4px;
            }}
            QPushButton#BtnCiclo {{
                background-color: #0B152B; color: #CBD5E1; border-radius: 10px;
                border: 1px solid #1A2B4C; text-align: left; padding: 9px 12px; font-size: 12px; font-weight: 600;
            }}
            QPushButton#BtnCiclo:hover {{
                background-color: #17274E; color: #FFFFFF; border: 1px solid {COR_AZUL_CLARO};
            }}
            QPushButton#BtnCicloAtivo {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0066FF, stop:1 #1D4ED8);
                color: #FFFFFF; font-weight: bold; border: 1px solid #60A5FA;
                border-radius: 10px; text-align: left; padding: 9px 12px; font-size: 12px;
            }}
            QPushButton#BtnMenuLateral {{
                background-color: #10192A; color: #CBD5E1; border-radius: 10px;
                border: 1px solid #1E3360; text-align: left; padding: 8px 12px; font-size: 12px; font-weight: 700;
            }}
            QPushButton#BtnMenuLateral:hover {{
                background-color: #1C2E52; border: 1px solid {COR_AZUL_CLARO}; color: #FFFFFF;
            }}
            QPushButton#BtnMenuLateralAtivo {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #1D4ED8);
                color: #FFFFFF; border-radius: 10px; border: 1px solid #60A5FA;
                text-align: left; padding: 8px 12px; font-size: 12px; font-weight: bold;
            }}
            QPushButton#BtnConfigFin {{
                background-color: #1E293B; color: {COR_AZUL_CLARO}; border: 1px solid #334155;
                font-size: 11px; font-weight: bold; padding: 7px 12px; border-radius: 8px;
            }}
            QPushButton#BtnDemo {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10B981);
                color: #022C22; border: none; font-weight: 800; font-size: 11px; padding: 9px 12px; border-radius: 10px;
            }}
            QPushButton#BtnDemo:hover {{ background: #34D399; }}
            QPushButton#BtnReset {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #B91C1C, stop:1 #EF4444);
                color: #FFFFFF; border: none; font-weight: 800; font-size: 11px; padding: 9px 12px; border-radius: 10px;
            }}
            QPushButton#BtnReset:hover {{ background: #F87171; }}
            QTableWidget {{
                background-color: #080D1D; color: {COR_TEXTO}; gridline-color: #162443;
                border-radius: 10px; border: 1px solid #1A2B4C;
            }}
            QHeaderView::section {{
                background-color: #0E1A35; color: {COR_AZUL_CLARO}; font-weight: bold; border: none; padding: 6px;
            }}
            QTableWidget::item {{ padding: 5px; }}
            QProgressBar {{
                background-color: #070D1A; border-radius: 5px; border: 1px solid #1E2F52;
                text-align: center; color: #FFFFFF; font-size: 10px; font-weight: bold;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:1 #38BDF8);
                border-radius: 4px;
            }}
        """)

        carregar_configuracao_financeira()
        inicializar_banco()

        self.worker_alimentos = WorkerIAAlimentos()
        self.worker_alimentos.frame_processado.connect(self.atualizar_video_alimentos)
        self.worker_alimentos.evento_compra.connect(self.processar_compra)
        self.worker_alimentos.start()

        self.worker_facial = WorkerIAFacial()
        self.worker_facial.frame_processado.connect(self.atualizar_video_facial)
        self.worker_facial.start()

        self.cam_master = CameraMasterThread()
        self.cam_master.frame_cam0.connect(self.worker_alimentos.receber_frame)
        self.cam_master.frame_cam1.connect(self.worker_facial.receber_frame)
        self.cam_master.frame_cam1.connect(self.alimentar_stream_modal)
        self.cam_master.start()

        self.init_ui()
        self.definir_ciclo("Fundamental 1", 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.atualizar_dashboard)
        self.timer.start(2000)

    def alimentar_stream_modal(self, frame):
        if self.modal_cadastro_aberto and self.modal_cadastro_aberto.isVisible():
            self.modal_cadastro_aberto.receber_stream_ao_vivo(frame)

    def init_ui(self):
        widget_central = QWidget()
        layout_raiz = QHBoxLayout(widget_central)
        layout_raiz.setContentsMargins(14, 14, 14, 14)
        layout_raiz.setSpacing(14)

        layout_raiz.addWidget(self.criar_painel_controle())
        painel_central = self.criar_painel_central()
        layout_raiz.addWidget(painel_central, stretch=1)
        layout_raiz.addWidget(self.criar_painel_cameras_duplas())

        self.setCentralWidget(widget_central)
        self.mudar_aba(0)
        self.showMaximized()

    def criar_painel_controle(self):
        painel = QFrame()
        painel.setObjectName("CardPrincipal")
        painel.setFixedWidth(260)
        aplicar_sombra(painel, blur=32, cor=QColor(0, 102, 255, 35))
        layout_menu = QVBoxLayout(painel)
        layout_menu.setContentsMargins(14, 16, 14, 16)
        layout_menu.setSpacing(8)

        moldura_logo = QFrame()
        moldura_logo.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #112046, stop:1 #060B18);
                border: 2px solid #1E3A8A; border-radius: 14px;
            }
        """)
        aplicar_sombra(moldura_logo, blur=26, cor=QColor(0, 102, 255, 60))
        layout_moldura = QVBoxLayout(moldura_logo)
        layout_moldura.setContentsMargins(8, 10, 8, 8)
        layout_moldura.setSpacing(3)

        self.lbl_logo = QLabel()
        self.lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_logo.setFixedHeight(110)

        candidatos_logo = [
            os.path.join(PASTA_ASSETS, "logo.png"),
            os.path.join(PASTA_ASSETS, "logo.jpg"),
            os.path.join(PASTA_RAIZ, "logo.png")
        ]
        caminho_logo = next((c for c in candidatos_logo if os.path.exists(c)), None)

        if caminho_logo:
            pix = QPixmap(caminho_logo).scaled(220, 105, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.lbl_logo.setPixmap(pix)
            self.lbl_logo.setStyleSheet("background: transparent; border: none;")
        else:
            self.lbl_logo.setText("⚡ JPPN\nTECH")
            self.lbl_logo.setStyleSheet("font-size: 22px; font-weight: 900; color: #FFFFFF; background: transparent; border: none; letter-spacing: 2px;")

        lbl_sub = QLabel("Cantina Inteligente - JPPN")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_sub.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {COR_AZUL_CLARO}; background: transparent; border: none; letter-spacing: 1px;")

        layout_moldura.addWidget(self.lbl_logo)
        layout_moldura.addWidget(lbl_sub)
        layout_menu.addWidget(moldura_logo)

        linha1 = QFrame()
        linha1.setFixedHeight(1)
        linha1.setStyleSheet(f"background-color: {COR_BORDA}; margin-top: 2px; margin-bottom: 2px;")
        layout_menu.addWidget(linha1)

        lbl_ciclo = QLabel("FILTRAR POR TURMA")
        lbl_ciclo.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {COR_AZUL_CLARO}; letter-spacing: 1px;")
        layout_menu.addWidget(lbl_ciclo)

        self.btn_c0 = QPushButton("  Todas as Turmas")
        self.btn_c1 = QPushButton("  Fundamental 1")
        self.btn_c2 = QPushButton("  Fundamental 2")
        self.btn_c3 = QPushButton("  Ensino Médio")
        self.botoes_ciclo = [self.btn_c0, self.btn_c1, self.btn_c2, self.btn_c3]

        for b in self.botoes_ciclo:
            b.setObjectName("BtnCiclo")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            layout_menu.addWidget(b)

        self.btn_c0.clicked.connect(lambda: self.definir_ciclo("Todas", 0))
        self.btn_c1.clicked.connect(lambda: self.definir_ciclo("Fundamental 1", 1))
        self.btn_c2.clicked.connect(lambda: self.definir_ciclo("Fundamental 2", 2))
        self.btn_c3.clicked.connect(lambda: self.definir_ciclo("Ensino Médio", 3))

        linha2 = QFrame()
        linha2.setFixedHeight(1)
        linha2.setStyleSheet(f"background-color: {COR_BORDA}; margin-top: 2px; margin-bottom: 2px;")
        layout_menu.addWidget(linha2)

        lbl_gestao = QLabel("MÓDULOS DE GESTÃO B2B")
        lbl_gestao.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {COR_AMARELO}; letter-spacing: 1px;")
        layout_menu.addWidget(lbl_gestao)

        self.btn_aba_estoque = QPushButton("  Estoque ")
        self.btn_aba_estoque.setObjectName("BtnMenuLateral")
        self.btn_aba_estoque.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_aba_estoque.setIcon(carregar_ou_gerar_icone("estoque", "ESTQ", COR_AMARELO, tamanho=30))
        self.btn_aba_estoque.setIconSize(QSize(24, 24))
        self.btn_aba_estoque.clicked.connect(lambda: self.mudar_aba(7))
        layout_menu.addWidget(self.btn_aba_estoque)

        self.btn_aba_financeiro = QPushButton("  Financeiro")
        self.btn_aba_financeiro.setObjectName("BtnMenuLateral")
        self.btn_aba_financeiro.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_aba_financeiro.setIcon(carregar_ou_gerar_icone("financeiro", "$", COR_VERDE, tamanho=30))
        self.btn_aba_financeiro.setIconSize(QSize(24, 24))
        self.btn_aba_financeiro.clicked.connect(lambda: self.mudar_aba(8))
        layout_menu.addWidget(self.btn_aba_financeiro)

        self.btn_aba_escola = QPushButton("  Controle de Alunos")
        self.btn_aba_escola.setObjectName("BtnMenuLateral")
        self.btn_aba_escola.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_aba_escola.setIcon(carregar_ou_gerar_icone("alunos", "ESC", "#EC4899", tamanho=30))
        self.btn_aba_escola.setIconSize(QSize(24, 24))
        self.btn_aba_escola.clicked.connect(lambda: self.mudar_aba(9))
        layout_menu.addWidget(self.btn_aba_escola)

        layout_menu.addStretch()

        self.btn_demo = QPushButton(" Carga Demo")
        self.btn_demo.setObjectName("BtnDemo")
        self.btn_demo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_demo.setIcon(carregar_ou_gerar_icone("demo", "⚡", COR_VERDE, tamanho=24))
        self.btn_demo.setIconSize(QSize(20, 20))
        self.btn_demo.clicked.connect(self.executar_carga_demo)
        layout_menu.addWidget(self.btn_demo)

        self.btn_reset = QPushButton(" Reiniciar Auditoria")
        self.btn_reset.setObjectName("BtnReset")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setIcon(carregar_ou_gerar_icone("reset", "↺", "#FFFFFF", tamanho=24))
        self.btn_reset.setIconSize(QSize(20, 20))
        self.btn_reset.clicked.connect(self.confirmar_reset)
        layout_menu.addWidget(self.btn_reset)

        return painel

    def criar_painel_central(self):
        painel_central = QFrame()
        painel_central.setObjectName("CardPrincipal")
        aplicar_sombra(painel_central, blur=32, cor=QColor(0, 102, 255, 30))
        layout_central = QVBoxLayout(painel_central)
        layout_central.setContentsMargins(16, 14, 16, 14)
        layout_central.setSpacing(12)

        barra_nav = QFrame()
        barra_nav.setObjectName("BarraNavSuperior")
        layout_nav = QHBoxLayout(barra_nav)
        layout_nav.setContentsMargins(10, 8, 10, 8)
        layout_nav.setSpacing(12)
        layout_nav.setAlignment(Qt.AlignmentFlag.AlignLeft)

        abas_superiores = [
            ("visao", "DASH", "Visão Geral Executiva", COR_AZUL),
            ("in_natura", "NAT", "In Natura (Frutas/Saladas)", COR_VERDE),
            ("assado", "ASS", "Assados & Massas Forneadas", COR_LARANJA),
            ("fritura", "FRIT", "Frituras & Crocantes", COR_ROXO),
            ("doce", "DOCE", "Doces & Sobremesas", "#EC4899"),
            ("bebida", "BEB", "Bebidas Processadas", "#06B6D4"),
            ("comparativo", "COMP", "Comparativo Consolidado", "#3B82F6")
        ]

        self.botoes_abas = []
        for idx, (nome_arq, sigla, rotulo, cor_hex) in enumerate(abas_superiores):
            btn = QPushButton()
            btn.setObjectName("IconeAbaGrande")
            btn.setFixedSize(64, 64)
            btn.setToolTip(rotulo)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(carregar_ou_gerar_icone(nome_arq, sigla, cor_hex, tamanho=48))
            btn.setIconSize(QSize(44, 44))
            btn.clicked.connect(lambda _, i=idx: self.mudar_aba(i))
            layout_nav.addWidget(btn)
            self.botoes_abas.append(btn)

        layout_nav.addStretch()
        layout_central.addWidget(barra_nav)

        topo = QFrame()
        topo.setObjectName("CardHeader")
        layout_topo = QHBoxLayout(topo)
        layout_topo.setContentsMargins(16, 10, 16, 10)

        self.lbl_titulo_aba = QLabel("Visão Geral Executiva")
        self.lbl_titulo_aba.setStyleSheet(f"font-size: 16px; font-weight: 900; color: {COR_TEXTO};")
        layout_topo.addWidget(self.lbl_titulo_aba)
        layout_topo.addStretch()

        self.lbl_tag_ciclo = QLabel("Turma: Fundamental 1")
        self.lbl_tag_ciclo.setStyleSheet(f"""
            font-size: 11px; font-weight: bold; color: {COR_AZUL_CLARO};
            background-color: #0F2042; border-radius: 8px; padding: 6px 14px;
            border: 1px solid #1E3A8A; margin-right: 6px;
        """)
        layout_topo.addWidget(self.lbl_tag_ciclo)

        self.lbl_pill_status = QLabel("● AI Box Conectado")
        self.lbl_pill_status.setStyleSheet(f"""
            font-size: 11px; font-weight: bold; color: {COR_VERDE};
            background-color: #064E3B; border-radius: 8px; padding: 6px 14px;
            border: 1px solid #059669;
        """)
        layout_topo.addWidget(self.lbl_pill_status)

        layout_central.addWidget(topo)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.criar_tela_visao_geral())
        self.stack.addWidget(self.criar_tela_categoria("In Natura & Frutas", "in_natura", COR_VERDE))
        self.stack.addWidget(self.criar_tela_categoria("Assados & Massas", "assados", COR_LARANJA))
        self.stack.addWidget(self.criar_tela_categoria("Frituras & Crocantes", "frituras", COR_ROXO))
        self.stack.addWidget(self.criar_tela_categoria("Doces & Confeitaria", "doces", "#EC4899"))
        self.stack.addWidget(self.criar_tela_categoria("Bebidas Processadas", "bebidas_processadas", "#06B6D4"))
        self.stack.addWidget(self.criar_tela_comparativo())
        self.stack.addWidget(self.criar_tela_estoque())
        self.stack.addWidget(self.criar_tela_financeiro())
        self.stack.addWidget(self.criar_tela_gestao_escolar())

        layout_central.addWidget(self.stack, stretch=1)
        return painel_central

    def criar_painel_cameras_duplas(self):
        painel_cam = QFrame()
        painel_cam.setObjectName("CardPrincipal")
        painel_cam.setFixedWidth(380)
        aplicar_sombra(painel_cam, blur=28, cor=QColor(0, 102, 255, 30))
        layout_cam = QVBoxLayout(painel_cam)
        layout_cam.setContentsMargins(12, 12, 12, 12)
        layout_cam.setSpacing(8)

        lbl_cam0 = QLabel("📹 CAM 01: Balcão & Alimentos (YOLOv8)")
        lbl_cam0.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_AZUL_CLARO};")
        layout_cam.addWidget(lbl_cam0)

        moldura_cam0 = QFrame()
        moldura_cam0.setStyleSheet("background-color: #000; border-radius: 10px; border: 2px solid #1D4ED8;")
        l_m0 = QVBoxLayout(moldura_cam0)
        l_m0.setContentsMargins(2, 2, 2, 2)
        self.lbl_video_alimentos = QLabel()
        self.lbl_video_alimentos.setFixedSize(350, 200)
        self.lbl_video_alimentos.setStyleSheet("background-color: #000; border-radius: 8px;")
        self.lbl_video_alimentos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l_m0.addWidget(self.lbl_video_alimentos)
        layout_cam.addWidget(moldura_cam0)

        lbl_cam1 = QLabel("👤 CAM 02: Reconhecimento Facial (YOLOv8)")
        lbl_cam1.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_VERDE};")
        layout_cam.addWidget(lbl_cam1)

        moldura_cam1 = QFrame()
        moldura_cam1.setStyleSheet("background-color: #000; border-radius: 10px; border: 2px solid #059669;")
        l_m1 = QVBoxLayout(moldura_cam1)
        l_m1.setContentsMargins(2, 2, 2, 2)
        self.lbl_video_facial = QLabel()
        self.lbl_video_facial.setFixedSize(350, 200)
        self.lbl_video_facial.setStyleSheet("background-color: #000; border-radius: 8px;")
        self.lbl_video_facial.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l_m1.addWidget(self.lbl_video_facial)
        layout_cam.addWidget(moldura_cam1)

        lbl_tabela_tit = QLabel("Auditoria em Tempo Real (Recreio)")
        lbl_tabela_tit.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_TEXTO}; margin-top: 2px;")
        layout_cam.addWidget(lbl_tabela_tit)

        self.tabela_logs = QTableWidget(5, 4)
        self.tabela_logs.setHorizontalHeaderLabels(["Hora", "Aluno", "Saudáveis", "Proc."])
        self.tabela_logs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabela_logs.verticalHeader().setVisible(False)
        self.tabela_logs.setFixedHeight(125)
        layout_cam.addWidget(self.tabela_logs)

        return painel_cam

    def criar_card_kpi(self, titulo, valor_inicial, cor):
        card = QFrame()
        card.setObjectName("CardHeader")
        l = QVBoxLayout(card)
        l.setContentsMargins(12, 10, 12, 10)
        l.setSpacing(4)
        t = QLabel(titulo)
        t.setStyleSheet(f"font-size: 10px; font-weight: bold; color: {COR_TEXTO_SEC}; letter-spacing: 0.5px;")
        lbl_valor = QLabel(valor_inicial)
        lbl_valor.setStyleSheet(f"font-size: 20px; font-weight: 900; color: {cor};")
        barra = QFrame()
        barra.setFixedHeight(3)
        barra.setStyleSheet(f"background-color: {cor}; border-radius: 2px;")
        l.addWidget(t)
        l.addWidget(lbl_valor)
        l.addWidget(barra)
        return card, lbl_valor

    def criar_tela_visao_geral(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        grid_kpi = QGridLayout()
        grid_kpi.setSpacing(10)

        card1, pagina.lbl_kpi_total = self.criar_card_kpi("TOTAL AUDITADO (30D)", "0", COR_TEXTO)
        card2, pagina.lbl_kpi_saudavel = self.criar_card_kpi("ÍNDICE SAUDÁVEL", "0%", COR_VERDE)
        card3, pagina.lbl_kpi_proc = self.criar_card_kpi("ULTRAPROCESSADOS", "0%", COR_VERMELHO)
        card4, pagina.lbl_kpi_score = self.criar_card_kpi("SCORE CONFORMIDADE", "100/100", COR_AZUL_CLARO)

        for col, card in enumerate([card1, card2, card3, card4]):
            grid_kpi.addWidget(card, 0, col)

        layout.addLayout(grid_kpi)

        linha_graficos = QHBoxLayout()
        linha_graficos.setSpacing(10)
        pagina.canvas_pizza = GraficoModernoCanvas(self, width=3.8, height=3.2)
        pagina.canvas_tempo = GraficoModernoCanvas(self, width=4.8, height=3.2)

        linha_graficos.addWidget(pagina.canvas_pizza)
        linha_graficos.addWidget(pagina.canvas_tempo)
        layout.addLayout(linha_graficos, stretch=1)

        card_alerta = QFrame()
        card_alerta.setObjectName("CardHeader")
        l_alerta = QVBoxLayout(card_alerta)
        l_alerta.setContentsMargins(12, 10, 12, 10)
        l_alerta.setSpacing(4)

        l_topo_alerta = QHBoxLayout()
        pagina.lbl_diretriz = QLabel("● Relatório para Direção: Monitoramento ativo de diretrizes nutricionais escolares.")
        pagina.lbl_diretriz.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_VERDE};")
        l_topo_alerta.addWidget(pagina.lbl_diretriz)
        l_topo_alerta.addStretch()
        
        pagina.lbl_meta = QLabel("Meta Saudável: 70%")
        pagina.lbl_meta.setStyleSheet(f"font-size: 10px; font-weight: bold; color: {COR_TEXTO_SEC};")
        l_topo_alerta.addWidget(pagina.lbl_meta)
        l_alerta.addLayout(l_topo_alerta)

        pagina.barra_meta = QProgressBar()
        pagina.barra_meta.setFixedHeight(11)
        pagina.barra_meta.setValue(50)
        l_alerta.addWidget(pagina.barra_meta)

        layout.addWidget(card_alerta)
        return pagina

    def criar_tela_categoria(self, titulo, campo_dado, cor):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        lbl = QLabel(f"Relatório de Vendas e Giro · {titulo}")
        lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {COR_TEXTO_SEC};")
        layout.addWidget(lbl)

        grid = QGridLayout()
        grid.setSpacing(10)
        card1, pagina.lbl_hoje = self.criar_card_kpi("CONSUMO HOJE", "0 un.", cor)
        card2, pagina.lbl_30d = self.criar_card_kpi("TOTAL NO MÊS", "0 un.", COR_TEXTO)
        card3, pagina.lbl_participacao = self.criar_card_kpi("PRESENÇA DE BALCÃO", "0%", COR_AZUL_CLARO)

        for i, card in enumerate([card1, card2, card3]):
            grid.addWidget(card, 0, i)

        layout.addLayout(grid)

        pagina.canvas = GraficoModernoCanvas(self, width=6, height=3.5)
        layout.addWidget(pagina.canvas, stretch=1)
        pagina.campo_dado = campo_dado
        pagina.cor_destaque = cor
        return pagina

    def criar_tela_comparativo(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        lbl = QLabel("Comparativo de Comportamento Consolidado por Turma (30 Dias)")
        lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {COR_TEXTO_SEC};")
        layout.addWidget(lbl)

        pagina.canvas = GraficoModernoCanvas(self, width=6, height=3.8)
        layout.addWidget(pagina.canvas, stretch=1)
        return pagina

    def criar_tela_estoque(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        linha_cards = QHBoxLayout()
        linha_cards.setSpacing(12)

        card_top = QFrame()
        card_top.setObjectName("CardHeader")
        card_top.setStyleSheet(f"background-color: {COR_CARD_HEADER}; border: 1px solid #059669; border-radius: 12px;")
        l_top = QVBoxLayout(card_top)
        l_top.setContentsMargins(12, 10, 12, 10)
        l_top.setSpacing(4)
        
        lbl_t1 = QLabel("🔥 PRODUTOS MAIS VENDIDOS (ALTO GIRO)")
        lbl_t1.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_VERDE};")
        l_top.addWidget(lbl_t1)
        
        pagina.lbl_top1 = QLabel("1º Carregando...")
        pagina.lbl_top1.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF;")
        pagina.lbl_top2 = QLabel("2º Carregando...")
        pagina.lbl_top2.setStyleSheet("font-size: 12px; font-weight: 600; color: #CBD5E1;")
        pagina.lbl_top3 = QLabel("3º Carregando...")
        pagina.lbl_top3.setStyleSheet("font-size: 12px; font-weight: 600; color: #94A3B8;")
        
        l_top.addWidget(pagina.lbl_top1)
        l_top.addWidget(pagina.lbl_top2)
        l_top.addWidget(pagina.lbl_top3)
        linha_cards.addWidget(card_top)

        card_low = QFrame()
        card_low.setObjectName("CardHeader")
        card_low.setStyleSheet(f"background-color: {COR_CARD_HEADER}; border: 1px solid #DC2626; border-radius: 12px;")
        l_low = QVBoxLayout(card_low)
        l_low.setContentsMargins(12, 10, 12, 10)
        l_low.setSpacing(4)

        lbl_l1 = QLabel("⚠️ MENOR PROCURA (RISCO DE ESTUFA)")
        lbl_l1.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_VERMELHO};")
        l_low.addWidget(lbl_l1)

        pagina.lbl_low1 = QLabel("1º Carregando...")
        pagina.lbl_low1.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF;")
        pagina.lbl_low2 = QLabel("2º Carregando...")
        pagina.lbl_low2.setStyleSheet("font-size: 12px; font-weight: 600; color: #CBD5E1;")
        pagina.lbl_low3 = QLabel("3º Carregando...")
        pagina.lbl_low3.setStyleSheet("font-size: 12px; font-weight: 600; color: #94A3B8;")

        l_low.addWidget(pagina.lbl_low1)
        l_low.addWidget(pagina.lbl_low2)
        l_low.addWidget(pagina.lbl_low3)
        linha_cards.addWidget(card_low)

        layout.addLayout(linha_cards)

        card_fornada = QFrame()
        card_fornada.setObjectName("CardHeader")
        card_fornada.setStyleSheet(f"background-color: #0F1A30; border: 1px solid {COR_AZUL_CLARO}; border-radius: 12px;")
        l_fornada = QVBoxLayout(card_fornada)
        l_fornada.setContentsMargins(12, 10, 12, 10)
        
        lbl_fornada_tit = QLabel("⚡ PREVISÃO PREDITIVA DE FORNADA (PRÓXIMO RECREIO)")
        lbl_fornada_tit.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {COR_AZUL_CLARO};")
        pagina.lbl_fornada_desc = QLabel("Calculando produção recomendada...")
        pagina.lbl_fornada_desc.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {COR_TEXTO};")
        
        l_fornada.addWidget(lbl_fornada_tit)
        l_fornada.addWidget(pagina.lbl_fornada_desc)
        layout.addWidget(card_fornada)

        pagina.tabela_abc = QTableWidget(5, 6)
        pagina.tabela_abc.setHorizontalHeaderLabels([
            "Categoria / Item", "Volume", "Presença", "Faturamento", "Classe ABC", "Ação Operacional"
        ])
        pagina.tabela_abc.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        pagina.tabela_abc.verticalHeader().setVisible(False)
        layout.addWidget(pagina.tabela_abc, stretch=1)

        return pagina

    def criar_tela_financeiro(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        topo_fin = QHBoxLayout()
        lbl_sub_fin = QLabel("Controladoria de Balcão & Combate a Desvios de Caixa")
        lbl_sub_fin.setStyleSheet(f"font-size: 13px; font-weight: 800; color: {COR_TEXTO_SEC};")
        topo_fin.addWidget(lbl_sub_fin)
        topo_fin.addStretch()

        btn_ajustar_precos = QPushButton("Calibrar Preços & Custos")
        btn_ajustar_precos.setObjectName("BtnConfigFin")
        btn_ajustar_precos.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ajustar_precos.clicked.connect(self.abrir_modal_config_precos)
        topo_fin.addWidget(btn_ajustar_precos)

        layout.addLayout(topo_fin)

        grid_fin = QGridLayout()
        grid_fin.setSpacing(10)

        card1, pagina.lbl_fin_fat = self.criar_card_kpi("FATURAMENTO AUDITADO", "R$ 0,00", COR_VERDE)
        card2, pagina.lbl_fin_lucro = self.criar_card_kpi("LUCRO LÍQUIDO OPERACIONAL", "R$ 0,00", COR_AZUL_CLARO)
        card3, pagina.lbl_fin_ticket = self.criar_card_kpi("TICKET MÉDIO BALCÃO", "R$ 0,00", COR_TEXTO)
        card4, pagina.lbl_fin_desp = self.criar_card_kpi("ECONOMIA EM DESPERDÍCIO (EST.)", "R$ 0,00", COR_AMARELO)

        grid_fin.addWidget(card1, 0, 0)
        grid_fin.addWidget(card2, 0, 1)
        grid_fin.addWidget(card3, 0, 2)
        grid_fin.addWidget(card4, 0, 3)
        layout.addLayout(grid_fin)

        card_pdv = QFrame()
        card_pdv.setObjectName("CardHeader")
        card_pdv.setStyleSheet(f"background-color: #0A1428; border: 1px solid #1E3A8A; border-radius: 12px;")
        l_pdv = QHBoxLayout(card_pdv)
        l_pdv.setContentsMargins(16, 12, 16, 12)
        l_pdv.setSpacing(14)

        lbl_icone_pdv = QLabel("CONCILIAÇÃO PDV:")
        lbl_icone_pdv.setStyleSheet(f"font-size: 11px; font-weight: 900; color: {COR_AZUL_CLARO};")
        l_pdv.addWidget(lbl_icone_pdv)

        l_pdv.addWidget(QLabel("Valor Fechado no Caixa (R$):"))
        pagina.input_caixa = QDoubleSpinBox()
        pagina.input_caixa.setPrefix("R$ ")
        pagina.input_caixa.setRange(0.0, 500000.0)
        pagina.input_caixa.setSingleStep(50.0)
        pagina.input_caixa.setValue(0.0)
        pagina.input_caixa.valueChanged.connect(self.atualizar_dashboard)
        l_pdv.addWidget(pagina.input_caixa)

        pagina.lbl_divergencia = QLabel("Divergência / Furo de Caixa: R$ 0,00")
        pagina.lbl_divergencia.setStyleSheet(f"font-size: 13px; font-weight: 900; color: {COR_TEXTO_SEC};")
        l_pdv.addWidget(pagina.lbl_divergencia)

        l_pdv.addStretch()
        layout.addWidget(card_pdv)

        linha_graficos = QHBoxLayout()
        pagina.canvas_fin_dia = GraficoModernoCanvas(self, width=4.8, height=3.3)
        pagina.canvas_fin_share = GraficoModernoCanvas(self, width=3.8, height=3.3)
        linha_graficos.addWidget(pagina.canvas_fin_dia)
        linha_graficos.addWidget(pagina.canvas_fin_share)
        layout.addLayout(linha_graficos, stretch=1)

        return pagina

    def criar_tela_gestao_escolar(self):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        card_topo_gestao = QFrame()
        card_topo_gestao.setObjectName("CardHeader")
        card_topo_gestao.setStyleSheet(f"background-color: #0A1428; border: 1px solid #1D4ED8; border-radius: 12px;")
        l_tg = QHBoxLayout(card_topo_gestao)
        l_tg.setContentsMargins(14, 10, 14, 10)
        l_tg.setSpacing(10)

        lbl_desc = QLabel("Painel Escolar: Controle de Alunos & Envio de Relatórios")
        lbl_desc.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {COR_TEXTO};")
        l_tg.addWidget(lbl_desc)
        l_tg.addStretch()

        btn_cadastrar = QPushButton("➕ Cadastrar Aluno")
        btn_cadastrar.setObjectName("BtnDemo")
        btn_cadastrar.clicked.connect(self.abrir_modal_cadastro)
        l_tg.addWidget(btn_cadastrar)

        btn_enviar_relatorio = QPushButton("📨 Enviar Relatório Telegram")
        btn_enviar_relatorio.setStyleSheet("background-color: #0284C7; color: #FFFFFF; font-weight: bold; padding: 8px 12px; border-radius: 8px;")
        btn_enviar_relatorio.clicked.connect(self.enviar_relatorio_manual)
        l_tg.addWidget(btn_enviar_relatorio)

        btn_remover_um = QPushButton("❌ Excluir Selecionado")
        btn_remover_um.setStyleSheet("background-color: #991B1B; color: #FFFFFF; font-weight: bold; padding: 8px 12px; border-radius: 8px;")
        btn_remover_um.clicked.connect(self.excluir_aluno_selecionado)
        l_tg.addWidget(btn_remover_um)

        btn_remover_todos = QPushButton("🗑️ Apagar Todos")
        btn_remover_todos.setObjectName("BtnReset")
        btn_remover_todos.clicked.connect(self.confirmar_limpar_todos_alunos)
        l_tg.addWidget(btn_remover_todos)

        layout.addWidget(card_topo_gestao)

        self.tabela_alunos_gestao = QTableWidget(0, 6)
        self.tabela_alunos_gestao.setHorizontalHeaderLabels([
            "ID Aluno", "Nome do Aluno", "Turma", "Ultra Semana", "Teto Semanal", "Telegram Chat ID"
        ])
        self.tabela_alunos_gestao.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabela_alunos_gestao.verticalHeader().setVisible(False)
        self.tabela_alunos_gestao.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabela_alunos_gestao.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.tabela_alunos_gestao, stretch=1)

        return pagina

    def abrir_modal_config_precos(self):
        modal = ModalConfiguracaoPrecos(self)
        if modal.exec():
            self.atualizar_dashboard()
            QMessageBox.information(self, "Parâmetros Atualizados", "Novos preços e custos aplicados a todos os cálculos.")

    def abrir_modal_cadastro(self):
        modal = ModalCadastroAlunoFacial(parent=self)
        self.modal_cadastro_aberto = modal
        resultado = modal.exec()
        self.modal_cadastro_aberto = None

        if resultado == QDialog.DialogCode.Accepted:
            self.worker_facial.atualizar_lista()
            self.atualizar_dashboard()
            QMessageBox.information(self, "Sucesso", "Aluno cadastrado com reconhecimento facial e vinculado ao Telegram!")

    def enviar_relatorio_manual(self):
        linha = self.tabela_alunos_gestao.currentRow()
        if linha < 0:
            QMessageBox.warning(self, "Aviso", "Selecione um aluno na tabela para enviar o relatório.")
            return

        item_id = self.tabela_alunos_gestao.item(linha, 0)
        if not item_id:
            return

        alu_id = item_id.text()
        alunos = carregar_alunos()
        aluno_selecionado = next((a for a in alunos if a.get("id") == alu_id), None)

        if not aluno_selecionado:
            QMessageBox.warning(self, "Erro", "Aluno não encontrado na base de dados.")
            return

        chat_id = aluno_selecionado.get("telegram_chat_id", "").strip()
        if not chat_id:
            QMessageBox.warning(self, "Sem Telegram", f"O aluno {aluno_selecionado['nome']} não possui um Chat ID cadastrado.")
            return

        dados = carregar_dados()
        msg = gerar_relatorio_aluno(aluno_selecionado, dados, dias_atras=7)

        sucesso, resposta = enviar_mensagem_telegram_sincrona(TELEGRAM_BOT_TOKEN, chat_id, msg)
        if sucesso:
            QMessageBox.information(self, "Relatório Enviado", f"✅ Relatório entregue com sucesso no Telegram de {aluno_selecionado['responsavel']}!")
        else:
            QMessageBox.critical(self, "Falha no Envio Telegram", f"Não foi possível enviar:\n\n{resposta}")

    def excluir_aluno_selecionado(self):
        linha = self.tabela_alunos_gestao.currentRow()
        if linha < 0:
            QMessageBox.warning(self, "Aviso", "Selecione um aluno na tabela para excluir.")
            return

        item_id = self.tabela_alunos_gestao.item(linha, 0)
        item_nome = self.tabela_alunos_gestao.item(linha, 1)
        if not item_id or not item_nome:
            return

        alu_id = item_id.text()
        alu_nome = item_nome.text()

        resp = QMessageBox.question(
            self, "Confirmar Exclusão",
            f"Deseja realmente excluir o cadastro de {alu_nome} ({alu_id}) e sua foto facial?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if resp == QMessageBox.StandardButton.Yes:
            if excluir_aluno_por_id(alu_id):
                self.worker_facial.atualizar_lista()
                self.atualizar_dashboard()
                QMessageBox.information(self, "Excluído", f"Aluno {alu_nome} removido do sistema.")

    def confirmar_limpar_todos_alunos(self):
        resp = QMessageBox.question(
            self, "Apagar Todos os Cadastros",
            "ATENÇÃO: Deseja realmente excluir TODOS os alunos cadastrados e suas fotos?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if resp == QMessageBox.StandardButton.Yes:
            limpar_todos_alunos()
            self.worker_facial.atualizar_lista()
            self.atualizar_dashboard()
            QMessageBox.information(self, "Limpeza Concluída", "Todos os alunos foram removidos.")

    def mudar_aba(self, indice):
        self.stack.setCurrentIndex(indice)
        nomes_titulos = [
            "Visão Geral Executiva",
            "Categoria: In Natura & Frutas",
            "Categoria: Assados & Massas",
            "Categoria: Frituras & Crocantes",
            "Categoria: Doces & Confeitaria",
            "Categoria: Bebidas Processadas",
            "Comparativo Geral por Ciclo",
            "Inteligência de Estoque & Curva ABC",
            "Auditoria Financeira & Prevenção de Perdas",
            "Controle do Aluno e Relatórios aos Responsáveis"
        ]
        self.lbl_titulo_aba.setText(nomes_titulos[indice])

        for i, btn in enumerate(self.botoes_abas):
            btn.setObjectName("IconeAbaGrandeAtiva" if i == indice else "IconeAbaGrande")
            btn.setStyle(btn.style())

        self.btn_aba_estoque.setObjectName("BtnMenuLateralAtivo" if indice == 7 else "BtnMenuLateral")
        self.btn_aba_estoque.setStyle(self.btn_aba_estoque.style())

        self.btn_aba_financeiro.setObjectName("BtnMenuLateralAtivo" if indice == 8 else "BtnMenuLateral")
        self.btn_aba_financeiro.setStyle(self.btn_aba_financeiro.style())

        self.btn_aba_escola.setObjectName("BtnMenuLateralAtivo" if indice == 9 else "BtnMenuLateral")
        self.btn_aba_escola.setStyle(self.btn_aba_escola.style())

        self.atualizar_dashboard()

    def definir_ciclo(self, nome, indice):
        self.ciclo_selecionado = nome
        if nome != "Todas":
            self.lbl_tag_ciclo.setText(f"Turma: {nome}")
        else:
            self.lbl_tag_ciclo.setText("Turma: Todas (Global)")
            
        for i, b in enumerate(self.botoes_ciclo):
            b.setObjectName("BtnCicloAtivo" if i == indice else "BtnCiclo")
            b.setStyle(b.style())
            
        self.atualizar_dashboard()

    def processar_compra(self, contagens):
        registrar_compra(
            contagens.get("in_natura", 0), contagens.get("assados", 0),
            contagens.get("frituras", 0), contagens.get("doces", 0),
            contagens.get("bebidas_processadas", 0), self.ciclo_selecionado,
            self.aluno_em_foco
        )
        if self.aluno_em_foco:
            self.verificar_teto_e_disparar_telegram(self.aluno_em_foco)

    def verificar_teto_e_disparar_telegram(self, aluno):
        dados = carregar_dados()
        limite_semana = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        total_ultra = sum(d.get("itens_processados", 0) for d in dados if d.get("aluno_id") == aluno["id"] and d.get("data", "") >= limite_semana)
        teto = aluno.get("limite_semanal", 4)
        chat_id = aluno.get("telegram_chat_id", "").strip()

        if total_ultra >= teto and aluno["id"] not in self.alertas_disparados:
            self.alertas_disparados.add(aluno["id"])
            msg = gerar_relatorio_aluno(aluno, dados, dias_atras=7)
            disparar_alerta_telegram(TELEGRAM_BOT_TOKEN, chat_id, msg)

    def executar_carga_demo(self):
        global CACHE_DADOS, CONFIG_FINANCEIRO
        registros = []
        data_base = datetime.now()
        p = CONFIG_FINANCEIRO["precos"]
        alunos = carregar_alunos()

        dias_uteis = []
        dias_retroativos = 1
        while len(dias_uteis) < 22:
            d = data_base - timedelta(days=dias_retroativos)
            if d.weekday() < 5:
                dias_uteis.append(d)
            dias_retroativos += 1

        for d in dias_uteis:
            str_data = d.strftime("%Y-%m-%d")

            for _ in range(random.randint(6, 9)):
                aluno = random.choice([a for a in alunos if a["turma"] == "Fundamental 1"]) if [a for a in alunos if a["turma"] == "Fundamental 1"] else (random.choice(alunos) if alunos else None)
                alu_id = aluno["id"] if aluno else "ALU-AVULSO"
                alu_nome = aluno["nome"] if aluno else "Consumo Avulso"

                h = f"{random.randint(9, 10):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"
                qtd_in = random.choices([1, 2, 3], weights=[0.4, 0.4, 0.2])[0]
                qtd_as = random.choices([1, 2], weights=[0.6, 0.4])[0]
                qtd_fr = 0
                qtd_dc = 1 if random.random() < 0.1 else 0
                qtd_bb = 1 if random.random() < 0.05 else 0

                fat = (qtd_in * p["in_natura"] + qtd_as * p["assados"] + qtd_dc * p["doces"] + qtd_bb * p["bebidas_processadas"])
                registros.append({
                    "timestamp": f"{str_data} {h}", "data": str_data, "hora": h,
                    "ciclo": "Fundamental 1", "aluno_id": alu_id, "aluno_nome": alu_nome,
                    "in_natura": qtd_in, "assados": qtd_as, "frituras": qtd_fr,
                    "doces": qtd_dc, "bebidas_processadas": qtd_bb,
                    "itens_saudaveis": qtd_in + qtd_as, "itens_processados": qtd_fr + qtd_dc + qtd_bb,
                    "faturamento_estimado": round(fat, 2)
                })

            for _ in range(random.randint(10, 14)):
                aluno = random.choice([a for a in alunos if a["turma"] == "Fundamental 2"]) if [a for a in alunos if a["turma"] == "Fundamental 2"] else (random.choice(alunos) if alunos else None)
                alu_id = aluno["id"] if aluno else "ALU-AVULSO"
                alu_nome = aluno["nome"] if aluno else "Consumo Avulso"

                h = f"{random.randint(10, 11):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"
                qtd_in = 1 if random.random() < 0.3 else 0
                qtd_as = random.randint(1, 2)
                qtd_fr = 1 if random.random() < 0.5 else 0
                qtd_dc = 1 if random.random() < 0.4 else 0
                qtd_bb = 1 if random.random() < 0.5 else 0

                fat = (qtd_in * p["in_natura"] + qtd_as * p["assados"] + qtd_fr * p["frituras"] + qtd_dc * p["doces"] + qtd_bb * p["bebidas_processadas"])
                registros.append({
                    "timestamp": f"{str_data} {h}", "data": str_data, "hora": h,
                    "ciclo": "Fundamental 2", "aluno_id": alu_id, "aluno_nome": alu_nome,
                    "in_natura": qtd_in, "assados": qtd_as, "frituras": qtd_fr,
                    "doces": qtd_dc, "bebidas_processadas": qtd_bb,
                    "itens_saudaveis": qtd_in + qtd_as, "itens_processados": qtd_fr + qtd_dc + qtd_bb,
                    "faturamento_estimado": round(fat, 2)
                })

            for _ in range(random.randint(18, 24)):
                aluno = random.choice([a for a in alunos if a["turma"] == "Ensino Médio"]) if [a for a in alunos if a["turma"] == "Ensino Médio"] else (random.choice(alunos) if alunos else None)
                alu_id = aluno["id"] if aluno else "ALU-AVULSO"
                alu_nome = aluno["nome"] if aluno else "Consumo Avulso"

                h = f"{random.randint(11, 12):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}"
                qtd_in = 0
                qtd_as = 1 if random.random() < 0.15 else 0
                qtd_fr = random.randint(2, 4)
                qtd_dc = random.randint(1, 2)
                qtd_bb = random.randint(1, 2)

                fat = (qtd_in * p["in_natura"] + qtd_as * p["assados"] + qtd_fr * p["frituras"] + qtd_dc * p["doces"] + qtd_bb * p["bebidas_processadas"])
                registros.append({
                    "timestamp": f"{str_data} {h}", "data": str_data, "hora": h,
                    "ciclo": "Ensino Médio", "aluno_id": alu_id, "aluno_nome": alu_nome,
                    "in_natura": qtd_in, "assados": qtd_as, "frituras": qtd_fr,
                    "doces": qtd_dc, "bebidas_processadas": qtd_bb,
                    "itens_saudaveis": qtd_in + qtd_as, "itens_processados": qtd_fr + qtd_dc + qtd_bb,
                    "faturamento_estimado": round(fat, 2)
                })

        registros.sort(key=lambda x: x["timestamp"])

        with LOCK_BANCO:
            try:
                with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
                    json.dump({"registros": registros}, f, indent=4)
                CACHE_DADOS = list(registros)
            except Exception as e:
                logging.error(f"Erro demo: {e}")
                return

        self.atualizar_dashboard()
        QMessageBox.information(self, "Carga Demo Concluída", "Dados analíticos de 30 dias gerados com sucesso!")

    def confirmar_reset(self):
        global CACHE_DADOS
        resposta = QMessageBox.question(
            self, "Limpar Registros", "Deseja zerar o histórico da cantina?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if resposta == QMessageBox.StandardButton.Yes:
            with LOCK_BANCO:
                try:
                    with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
                        json.dump({"registros": []}, f, indent=4)
                    CACHE_DADOS = []
                except Exception:
                    pass
            self.tabela_logs.clearContents()
            self.atualizar_dashboard()

    def atualizar_video_alimentos(self, frame, contagens):
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        q_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(q_img).scaled(self.lbl_video_alimentos.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.lbl_video_alimentos.setPixmap(pix)

    def atualizar_video_facial(self, frame, aluno_ativo):
        self.aluno_em_foco = aluno_ativo
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        q_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(q_img).scaled(self.lbl_video_facial.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.lbl_video_facial.setPixmap(pix)

    def atualizar_dashboard(self):
        todos_dados = carregar_dados()
        hoje = datetime.now().strftime("%Y-%m-%d")

        if self.ciclo_selecionado != "Todas":
            dados_filtrados = [d for d in todos_dados if d.get("ciclo") == self.ciclo_selecionado]
        else:
            dados_filtrados = todos_dados

        ultimos = todos_dados[-5:][::-1]
        for row in range(5):
            if row < len(ultimos):
                item = ultimos[row]
                self.tabela_logs.setItem(row, 0, QTableWidgetItem(item.get("hora", "-")))
                self.tabela_logs.setItem(row, 1, QTableWidgetItem(item.get("aluno_nome", "Não Identificado")))
                self.tabela_logs.setItem(row, 2, QTableWidgetItem(str(item.get("itens_saudaveis", 0))))
                self.tabela_logs.setItem(row, 3, QTableWidgetItem(str(item.get("itens_processados", 0))))
            else:
                for col in range(4):
                    self.tabela_logs.setItem(row, col, QTableWidgetItem("-"))

        # --- ABA 0: VISÃO GERAL ---
        tela0 = self.stack.widget(0)
        tot_s = sum(d.get("itens_saudaveis", 0) for d in dados_filtrados)
        tot_p = sum(d.get("itens_processados", 0) for d in dados_filtrados)
        total = tot_s + tot_p

        pct_s = int((tot_s / total * 100)) if total > 0 else 0
        pct_p = int((tot_p / total * 100)) if total > 0 else 0
        score = calcular_score_nutricional(dados_filtrados)

        tela0.lbl_kpi_total.setText(f"{total} un.")
        tela0.lbl_kpi_saudavel.setText(f"{pct_s}%")
        tela0.lbl_kpi_proc.setText(f"{pct_p}%")
        tela0.lbl_kpi_score.setText(f"{score}/100")
        tela0.barra_meta.setValue(pct_s)

        ax_p = tela0.canvas_pizza.axes
        ax_p.clear()
        if total > 0:
            ax_p.pie([tot_s, tot_p], labels=['Saudável', 'Ultraproc.'], autopct='%1.0f%%',
                     colors=[COR_VERDE, COR_VERMELHO], startangle=90, pctdistance=0.75,
                     wedgeprops=dict(width=0.45, edgecolor=COR_CARD, linewidth=2),
                     textprops={'color': COR_TEXTO, 'fontsize': 9, 'weight': 'bold'})
            titulo_pizza = f"Equilíbrio · {self.ciclo_selecionado}" if self.ciclo_selecionado != "Todas" else "Equilíbrio Global"
            ax_p.set_title(titulo_pizza, color=COR_AZUL_CLARO, fontsize=10, weight='bold')
        else:
            ax_p.text(0.5, 0.5, 'Sem dados para esta turma', color=COR_TEXTO_SEC, ha='center', va='center')
        tela0.canvas_pizza.draw()

        ax_t = tela0.canvas_tempo.axes
        ax_t.clear()
        dias_completos = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        dias_labels = [datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m") for d in dias_completos]
        
        s_por_dia = [sum(d.get("itens_saudaveis", 0) for d in dados_filtrados if d.get("data") == data_iso) for data_iso in dias_completos]
        p_por_dia = [sum(d.get("itens_processados", 0) for d in dados_filtrados if d.get("data") == data_iso) for data_iso in dias_completos]

        ax_t.plot(dias_labels, s_por_dia, marker='o', color=COR_VERDE, label='Saudáveis', linewidth=2)
        ax_t.plot(dias_labels, p_por_dia, marker='o', color=COR_VERMELHO, label='Ultraprocessados', linewidth=2)
        ax_t.tick_params(colors=COR_TEXTO_SEC, labelsize=7)
        ax_t.legend(facecolor=COR_CARD_HEADER, edgecolor=COR_BORDA, labelcolor=COR_TEXTO, fontsize=7)
        ax_t.set_title(f"Evolução (7 Dias) · {self.ciclo_selecionado}", color=COR_AZUL_CLARO, fontsize=10, weight='bold')
        tela0.canvas_tempo.draw()

        # --- ABAS 1 A 5: CATEGORIAS INDIVIDUAIS ---
        total_geral = sum(d.get("itens_saudaveis", 0) + d.get("itens_processados", 0) for d in todos_dados)
        for idx in range(1, 6):
            tela = self.stack.widget(idx)
            campo = tela.campo_dado
            cor = tela.cor_destaque

            dados_hoje = [d for d in todos_dados if d.get("data") == hoje]
            v_hoje = sum(d.get(campo, 0) for d in dados_hoje)
            v_30d = sum(d.get(campo, 0) for d in todos_dados)
            pct_part = (v_30d / total_geral * 100) if total_geral > 0 else 0

            tela.lbl_hoje.setText(f"{v_hoje} un.")
            tela.lbl_30d.setText(f"{v_30d} un.")
            tela.lbl_participacao.setText(f"{pct_part:.1f}%")

            ax = tela.canvas.axes
            ax.clear()
            turmas = ["Fundamental 1", "Fundamental 2", "Ensino Médio"]
            vals = [sum(d.get(campo, 0) for d in todos_dados if d.get("ciclo") == t) for t in turmas]

            ax.bar(["Fund 1", "Fund 2", "Médio"], vals, color=cor, width=0.45, edgecolor=COR_BORDA)
            ax.tick_params(colors=COR_TEXTO_SEC, labelsize=8)
            ax.set_title("Volume Real Auditado por Turma (30 Dias)", color=COR_AZUL_CLARO, fontsize=10, weight='bold')
            tela.canvas.draw()

        # --- ABA 6: COMPARATIVO ---
        tela6 = self.stack.widget(6)
        ax6 = tela6.canvas.axes
        ax6.clear()
        turmas = ["Fundamental 1", "Fundamental 2", "Ensino Médio"]
        s_list = [sum(d.get("itens_saudaveis", 0) for d in todos_dados if d.get("ciclo") == t) for t in turmas]
        p_list = [sum(d.get("itens_processados", 0) for d in todos_dados if d.get("ciclo") == t) for t in turmas]

        x = np.arange(len(turmas))
        w = 0.35
        ax6.bar(x - w / 2, s_list, w, label='Saudáveis', color=COR_VERDE, edgecolor=COR_BORDA)
        ax6.bar(x + w / 2, p_list, w, label='Ultraprocessados', color=COR_VERMELHO, edgecolor=COR_BORDA)
        ax6.set_xticks(x)
        ax6.set_xticklabels(["Fund 1", "Fund 2", "Médio"], color=COR_TEXTO_SEC, fontsize=9)
        ax6.tick_params(colors=COR_TEXTO_SEC)
        ax6.legend(facecolor=COR_CARD_HEADER, edgecolor=COR_BORDA, labelcolor=COR_TEXTO)
        ax6.set_title("Equilíbrio Global por Turma (30 Dias)", color=COR_AZUL_CLARO, fontsize=11, weight='bold')
        tela6.canvas.draw()

        # --- ABA 7: ESTOQUE, PÓDIO & CURVA ABC ---
        tela7 = self.stack.widget(7)
        cats = ["in_natura", "assados", "frituras", "doces", "bebidas_processadas"]
        totais_cat = {c: sum(d.get(c, 0) for d in dados_filtrados) for c in cats}
        ranking = sorted(totais_cat.items(), key=lambda item: item[1], reverse=True)
        tot_auditado_ciclo = sum(totais_cat.values())

        if tot_auditado_ciclo > 0:
            p1, v1 = ranking[0]
            p2, v2 = ranking[1]
            p3, v3 = ranking[2]
            tela7.lbl_top1.setText(f"🔥 1º {ROTULOS_CATEGORIAS[p1]} — {v1} un. ({(v1/tot_auditado_ciclo*100):.1f}%)")
            tela7.lbl_top2.setText(f"🔥 2º {ROTULOS_CATEGORIAS[p2]} — {v2} un. ({(v2/tot_auditado_ciclo*100):.1f}%)")
            tela7.lbl_top3.setText(f"🔥 3º {ROTULOS_CATEGORIAS[p3]} — {v3} un. ({(v3/tot_auditado_ciclo*100):.1f}%)")

            l1, lv1 = ranking[-1]
            l2, lv2 = ranking[-2]
            l3, lv3 = ranking[-3]
            tela7.lbl_low1.setText(f"🔻 1º {ROTULOS_CATEGORIAS[l1]} — Apenas {lv1} un. ({(lv1/tot_auditado_ciclo*100):.1f}%)")
            tela7.lbl_low2.setText(f"🔻 2º {ROTULOS_CATEGORIAS[l2]} — Apenas {lv2} un. ({(lv2/tot_auditado_ciclo*100):.1f}%)")
            tela7.lbl_low3.setText(f"🔻 3º {ROTULOS_CATEGORIAS[l3]} — Apenas {lv3} un. ({(lv3/tot_auditado_ciclo*100):.1f}%)")

            dias_base = 22
            rec_assados = int(np.ceil((totais_cat["assados"] / dias_base) * 1.10))
            rec_frituras = int(np.ceil((totais_cat["frituras"] / dias_base) * 1.10))
            rec_natura = int(np.ceil((totais_cat["in_natura"] / dias_base) * 1.10))
            rec_bebidas = int(np.ceil((totais_cat["bebidas_processadas"] / dias_base) * 1.10))

            tela7.lbl_fornada_desc.setText(
                f"Fornada sugerida: Assados: {rec_assados} un. | Frituras: {rec_frituras} un. | "
                f"In Natura: {rec_natura} un. | Bebidas: {rec_bebidas} un. (+10% margem contra filas)."
            )

            acumulado_pct = 0.0
            for r_idx, (cat_chave, vol) in enumerate(ranking):
                share = (vol / tot_auditado_ciclo) * 100
                acumulado_pct += share
                faturamento_cat = vol * CONFIG_FINANCEIRO["precos"].get(cat_chave, 0.0)

                if acumulado_pct <= 75:
                    classe = "Classe A (Alto)"
                    acao = "Reposição Contínua"
                elif acumulado_pct <= 92:
                    classe = "Classe B (Médio)"
                    acao = "Fornadas Fracionadas"
                else:
                    classe = "Classe C (Baixo)"
                    acao = "Reduzir Lote na Estufa"

                tela7.tabela_abc.setItem(r_idx, 0, QTableWidgetItem(ROTULOS_CATEGORIAS[cat_chave]))
                tela7.tabela_abc.setItem(r_idx, 1, QTableWidgetItem(f"{vol} un."))
                tela7.tabela_abc.setItem(r_idx, 2, QTableWidgetItem(f"{share:.1f}%"))
                tela7.tabela_abc.setItem(r_idx, 3, QTableWidgetItem(f"R$ {faturamento_cat:,.2f}"))
                tela7.tabela_abc.setItem(r_idx, 4, QTableWidgetItem(classe))
                tela7.tabela_abc.setItem(r_idx, 5, QTableWidgetItem(acao))

        # --- ABA 8: AUDITORIA FINANCEIRA & CONCILIAÇÃO PDV ---
        tela8 = self.stack.widget(8)
        p = CONFIG_FINANCEIRO["precos"]
        c_pct = CONFIG_FINANCEIRO["custos_pct"]

        faturamento_total = 0.0
        custo_insumos_total = 0.0

        for cat in cats:
            vol_cat = totais_cat[cat]
            preco_cat = p.get(cat, 0.0)
            fat_cat = vol_cat * preco_cat
            custo_cat = fat_cat * (c_pct.get(cat, 40.0) / 100.0)
            
            faturamento_total += fat_cat
            custo_insumos_total += custo_cat

        lucro_liquido_total = faturamento_total - custo_insumos_total
        total_transacoes = len(dados_filtrados)
        ticket_medio = (faturamento_total / total_transacoes) if total_transacoes > 0 else 0.0
        desperdicio_evitado = faturamento_total * 0.065

        tela8.lbl_fin_fat.setText(f"R$ {faturamento_total:,.2f}")
        tela8.lbl_fin_lucro.setText(f"R$ {lucro_liquido_total:,.2f}")
        tela8.lbl_fin_ticket.setText(f"R$ {ticket_medio:.2f}")
        tela8.lbl_fin_desp.setText(f"R$ {desperdicio_evitado:,.2f}")

        valor_caixa_declarado = tela8.input_caixa.value()
        if valor_caixa_declarado > 0:
            divergencia = valor_caixa_declarado - faturamento_total
            if divergencia < 0:
                tela8.lbl_divergencia.setText(f"⚠️ Sangria / Furo Detectado: R$ {abs(divergencia):,.2f}")
                tela8.lbl_divergencia.setStyleSheet(f"font-size: 13px; font-weight: 900; color: {COR_VERMELHO};")
            else:
                tela8.lbl_divergencia.setText(f"✅ Caixa Conciliado (Sobra: +R$ {divergencia:,.2f})")
                tela8.lbl_divergencia.setStyleSheet(f"font-size: 13px; font-weight: 900; color: {COR_VERDE};")
        else:
            tela8.lbl_divergencia.setText("Insira o valor fechado no caixa para conciliar")
            tela8.lbl_divergencia.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {COR_TEXTO_SEC};")

        ax_fd = tela8.canvas_fin_dia.axes
        ax_fd.clear()
        dias_completos = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        dias_labels = [datetime.strptime(d, "%Y-%m-%d").strftime("%d/%m") for d in dias_completos]
        
        fat_dia = []
        lucro_dia = []
        for data_iso in dias_completos:
            regs_dia = [d for d in dados_filtrados if d.get("data") == data_iso]
            f_d = sum(sum(r.get(c, 0) * p.get(c, 0.0) for c in cats) for r in regs_dia)
            c_d = sum(sum(r.get(c, 0) * p.get(c, 0.0) * (c_pct.get(c, 40.0) / 100.0) for c in cats) for r in regs_dia)
            fat_dia.append(f_d)
            lucro_dia.append(f_d - c_d)

        x = np.arange(len(dias_labels))
        w = 0.35
        ax_fd.bar(x - w / 2, fat_dia, w, label='Faturamento', color=COR_VERDE, edgecolor=COR_BORDA)
        ax_fd.bar(x + w / 2, lucro_dia, w, label='Lucro Líquido', color=COR_AZUL_CLARO, edgecolor=COR_BORDA)
        ax_fd.set_xticks(x)
        ax_fd.set_xticklabels(dias_labels, color=COR_TEXTO_SEC, fontsize=8)
        ax_fd.tick_params(colors=COR_TEXTO_SEC, labelsize=7)
        ax_fd.legend(facecolor=COR_CARD_HEADER, edgecolor=COR_BORDA, labelcolor=COR_TEXTO, fontsize=7)
        ax_fd.set_title("Faturamento vs Lucro Líquido (Últimos 7 Dias)", color=COR_AZUL_CLARO, fontsize=10, weight='bold')
        tela8.canvas_fin_dia.draw()

        ax_fs = tela8.canvas_fin_share.axes
        ax_fs.clear()
        fats_por_cat = [totais_cat[c] * p.get(c, 0.0) for c in cats]
        nomes_curtos = ["Natura", "Assados", "Frituras", "Doces", "Bebidas"]
        cores_cats = [COR_VERDE, COR_LARANJA, COR_ROXO, "#EC4899", "#06B6D4"]

        if sum(fats_por_cat) > 0:
            ax_fs.pie(fats_por_cat, labels=nomes_curtos, autopct='%1.0f%%',
                      colors=cores_cats, startangle=140, pctdistance=0.75,
                      wedgeprops=dict(width=0.45, edgecolor=COR_CARD, linewidth=2),
                      textprops={'color': COR_TEXTO, 'fontsize': 8, 'weight': 'bold'})
            ax_fs.set_title("Distribuição da Receita Auditada", color=COR_AZUL_CLARO, fontsize=10, weight='bold')
        else:
            ax_fs.text(0.5, 0.5, 'Sem faturamento no período', color=COR_TEXTO_SEC, ha='center', va='center')
        tela8.canvas_fin_share.draw()

        # --- ABA 9: GESTÃO ESCOLAR ---
        alunos = carregar_alunos()
        limite_semana = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        if hasattr(self, "tabela_alunos_gestao"):
            self.tabela_alunos_gestao.setRowCount(len(alunos))
            for idx, al in enumerate(alunos):
                p_al = sum(d.get("itens_processados", 0) for d in todos_dados if d.get("aluno_id") == al["id"] and d.get("data", "") >= limite_semana)
                teto = al.get("limite_semanal", 4)
                chat_id_exibicao = al.get("telegram_chat_id", "Não informado")

                self.tabela_alunos_gestao.setItem(idx, 0, QTableWidgetItem(al["id"]))
                self.tabela_alunos_gestao.setItem(idx, 1, QTableWidgetItem(al["nome"]))
                self.tabela_alunos_gestao.setItem(idx, 2, QTableWidgetItem(al["turma"]))
                self.tabela_alunos_gestao.setItem(idx, 3, QTableWidgetItem(f"{p_al} un."))
                self.tabela_alunos_gestao.setItem(idx, 4, QTableWidgetItem(f"{teto} un."))
                self.tabela_alunos_gestao.setItem(idx, 5, QTableWidgetItem(chat_id_exibicao))

    def closeEvent(self, e):
        self.cam_master.parar()
        self.worker_alimentos.parar()
        self.worker_facial.parar()
        e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = CantinaApp()
    win.show()
    sys.exit(app.exec())