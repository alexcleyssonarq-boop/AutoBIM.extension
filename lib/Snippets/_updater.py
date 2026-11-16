# -*- coding: utf-8 -*-
"""Verificador e Auto-Atualizador do AutoBIM via GitHub Releases / main."""

import os
import re
import time
import json
import codecs
import shutil
import zipfile
import tempfile

try:
    unicode
except NameError:
    unicode = str

try:
    import clr
    clr.AddReference("System")
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    import System
    from System.Net import HttpWebRequest, ServicePointManager, SecurityProtocolType
    from System.IO import StreamReader, FileStream, FileMode
    from System.Threading import Thread, ThreadStart
    from System.Windows import Visibility as WpfVisibility
    from System.Windows.Markup import XamlReader
    from pyrevit import forms
    HAS_UI = True
except Exception:
    HAS_UI = False


EXT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VERSION_JSON_PATH = os.path.join(EXT_ROOT, "version.json")
PREFS_PATH = os.path.join(os.path.dirname(__file__), "_updater_settings.json")

DEFAULT_OWNER = "alexcleyssonarq-boop"
DEFAULT_REPO = "AutoBIM.extension"
DEFAULT_BRANCH = "main"

TEMP_DIR = tempfile.gettempdir()
REMOTE_CACHE_PATH = os.path.join(TEMP_DIR, "AutoBIM_remote_info.json")
CHECK_INTERVAL_SEC = 12 * 3600


# =============================================================================
# FUNCOES PURAS DE VERSAO E METADADOS GITHUB (TESTAVEIS SEM REVIT)
# =============================================================================
def parse_semver(ver_str):
    """Converte string de versao (ex. 'v1.2.10') em tupla inteira (1, 2, 10)."""
    s = (ver_str or u"").strip()
    m = re.findall(r"\d+", s)
    if not m:
        return (0, 0, 0)
    nums = [int(x) for x in m[:4]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def comparar_versoes(v_local, v_remota):
    """Retorna 1 se v_remota > v_local, 0 se igual, -1 se menor."""
    a = parse_semver(v_local)
    b = parse_semver(v_remota)
    n = max(len(a), len(b))
    a_pad = a + (0,) * (n - len(a))
    b_pad = b + (0,) * (n - len(b))
    if b_pad > a_pad:
        return 1
    if b_pad < a_pad:
        return -1
    return 0


def ler_version_local(path=None):
    p = path or VERSION_JSON_PATH
    padrao = {
        "version": "1.0.0",
        "release_date": "",
        "repo_owner": DEFAULT_OWNER,
        "repo_name": DEFAULT_REPO,
        "branch": DEFAULT_BRANCH,
        "notes": "",
    }
    try:
        if os.path.exists(p):
            with codecs.open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                padrao.update(data)
    except Exception:
        pass
    return padrao


def extrair_info_release_github(release_json_dict, owner=DEFAULT_OWNER, repo=DEFAULT_REPO):
    """Interpreta resposta JSON da API GitHub /releases/latest."""
    if not isinstance(release_json_dict, dict):
        return None
    tag = (release_json_dict.get("tag_name") or "").strip()
    if not tag:
        return None
    ver_clean = tag.lstrip("vV").strip()
    zip_url = (
        release_json_dict.get("zipball_url")
        or u"https://github.com/{}/{}/archive/refs/tags/{}.zip".format(owner, repo, tag)
    )
    return {
        "version": ver_clean,
        "tag": tag,
        "notes": (release_json_dict.get("body") or u"").strip(),
        "published_at": (release_json_dict.get("published_at") or u"")[:10],
        "zip_url": zip_url,
        "html_url": release_json_dict.get("html_url") or u"https://github.com/{}/{}".format(owner, repo),
        "source": "release",
    }


def carregar_prefs_updater():
    try:
        if os.path.exists(PREFS_PATH):
            with codecs.open(PREFS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {"auto_check": True, "skipped_version": ""}


def salvar_prefs_updater(dados):
    try:
        atual = carregar_prefs_updater()
        atual.update(dados)
        with codecs.open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(atual, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# =============================================================================
# REDE (.NET TLS 1.2 COMPATIVEL COM IRONPYTHON 2.7 / REVIT 2022-2026)
# =============================================================================
def _habilitar_tls12():
    if not HAS_UI:
        return
    try:
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
    except Exception:
        try:
            ServicePointManager.SecurityProtocol = System.Net.SecurityProtocolType(3072)
        except Exception:
            pass


def _http_get_json(url, timeout_ms=3500):
    if not HAS_UI:
        return None
    _habilitar_tls12()
    req = HttpWebRequest.Create(url)
    req.Method = "GET"
    req.UserAgent = "AutoBIM-pyRevit-Updater/1.0"
    req.Accept = "application/vnd.github.v3+json"
    req.Timeout = int(timeout_ms)
    req.ReadWriteTimeout = int(timeout_ms)
    resp = None
    try:
        resp = req.GetResponse()
        stream = resp.GetResponseStream()
        reader = StreamReader(stream, System.Text.Encoding.UTF8)
        txt = reader.ReadToEnd()
        reader.Close()
        return json.loads(txt)
    finally:
        if resp is not None:
            try:
                resp.Close()
            except Exception:
                pass


def _http_download_file(url, dest_path, timeout_ms=30000):
    _habilitar_tls12()
    req = HttpWebRequest.Create(url)
    req.Method = "GET"
    req.UserAgent = "AutoBIM-pyRevit-Updater/1.0"
    req.AllowAutoRedirect = True
    req.Timeout = int(timeout_ms)
    req.ReadWriteTimeout = int(timeout_ms)
    resp = None
    try:
        resp = req.GetResponse()
        in_stream = resp.GetResponseStream()
        out_stream = FileStream(dest_path, FileMode.Create)
        buf = System.Array.CreateInstance(System.Byte, 16384)
        while True:
            n = in_stream.Read(buf, 0, buf.Length)
            if n <= 0:
                break
            out_stream.Write(buf, 0, n)
        out_stream.Close()
        in_stream.Close()
        return True
    finally:
        if resp is not None:
            try:
                resp.Close()
            except Exception:
                pass


def buscar_info_remota(timeout_ms=3500):
    """
    Consulta GitHub /releases/latest e faz fallback para version.json na branch main.
    Retorna dict com has_update, local_version, remote_version, zip_url, notes, html_url, error.
    """
    loc = ler_version_local()
    owner = loc.get("repo_owner") or DEFAULT_OWNER
    repo = loc.get("repo_name") or DEFAULT_REPO
    branch = loc.get("branch") or DEFAULT_BRANCH
    v_loc = loc.get("version") or "1.0.0"

    info_remota = None
    # 1. Tenta Releases oficiais do GitHub
    try:
        api_url = u"https://api.github.com/repos/{}/{}/releases/latest".format(owner, repo)
        rel_data = _http_get_json(api_url, timeout_ms=timeout_ms)
        info_remota = extrair_info_release_github(rel_data, owner=owner, repo=repo)
    except Exception:
        info_remota = None

    # 2. Fallback: consulta version.json direto na branch main
    if info_remota is None:
        try:
            raw_url = u"https://raw.githubusercontent.com/{}/{}/{}/version.json?t={}".format(
                owner, repo, branch, int(time.time())
            )
            ver_data = _http_get_json(raw_url, timeout_ms=timeout_ms)
            if isinstance(ver_data, dict) and ver_data.get("version"):
                info_remota = {
                    "version": unicode(ver_data.get("version")).lstrip("vV").strip(),
                    "tag": u"v" + unicode(ver_data.get("version")).lstrip("vV").strip(),
                    "notes": unicode(ver_data.get("notes") or u""),
                    "published_at": unicode(ver_data.get("release_date") or u""),
                    "zip_url": u"https://github.com/{}/{}/archive/refs/heads/{}.zip".format(owner, repo, branch),
                    "html_url": u"https://github.com/{}/{}".format(owner, repo),
                    "source": "branch",
                }
        except Exception as ex:
            return {
                "ok": False,
                "has_update": False,
                "local_version": v_loc,
                "remote_version": v_loc,
                "error": unicode(ex),
            }

    if info_remota is None:
        return {
            "ok": False,
            "has_update": False,
            "local_version": v_loc,
            "remote_version": v_loc,
            "error": u"Não foi possível consultar o repositório no GitHub.",
        }

    tem_nova = comparar_versoes(v_loc, info_remota["version"]) > 0
    return {
        "ok": True,
        "has_update": tem_nova,
        "local_version": v_loc,
        "remote_version": info_remota["version"],
        "tag": info_remota["tag"],
        "notes": info_remota["notes"],
        "published_at": info_remota["published_at"],
        "zip_url": info_remota["zip_url"],
        "html_url": info_remota["html_url"],
        "source": info_remota["source"],
        "checked_at": time.time(),
        "error": u"",
    }


def _preservar_dados_locais():
    """Faz backup em memoria de preferencias *_settings.json e arquivo SP antes da atualizacao."""
    backups = {}
    if not os.path.isdir(EXT_ROOT):
        return backups
    for dirpath, dirnames, filenames in os.walk(EXT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "_cache_rfa", "Output")]
        for fn in filenames:
            if fn.endswith("_settings.json") or fn == "_AutoBIM_PeDireito_SP.txt":
                fp = os.path.join(dirpath, fn)
                rel = os.path.relpath(fp, EXT_ROOT)
                try:
                    with open(fp, "rb") as f:
                        backups[rel] = f.read()
                except Exception:
                    pass
    return backups


def _restaurar_dados_locais(backups):
    for rel, raw in backups.items():
        try:
            dest = os.path.join(EXT_ROOT, rel)
            d_parent = os.path.dirname(dest)
            if not os.path.isdir(d_parent):
                os.makedirs(d_parent)
            with open(dest, "wb") as f:
                f.write(raw)
        except Exception:
            pass


def aplicar_atualizacao_zip(zip_url):
    """Baixa o ZIP do GitHub e atualiza AutoBIM.extension preservando preferencias e _cache_rfa."""
    zip_path = os.path.join(TEMP_DIR, "AutoBIM_update_pkg.zip")
    ext_dir = os.path.join(TEMP_DIR, "AutoBIM_update_extract")

    for p in (zip_path, ext_dir):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass

    _http_download_file(zip_url, zip_path)
    if not os.path.isfile(zip_path) or os.path.getsize(zip_path) < 1024:
        raise RuntimeError(u"Arquivo de atualização vazio ou corrompido.")

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(ext_dir)

    # Encontra a raiz da extensao dentro do ZIP extraido (onde fica AutoBIM.tab)
    src_root = None
    if os.path.isdir(os.path.join(ext_dir, "AutoBIM.tab")):
        src_root = ext_dir
    else:
        for entry in os.listdir(ext_dir):
            cand = os.path.join(ext_dir, entry)
            if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, "AutoBIM.tab")):
                src_root = cand
                break
    if src_root is None:
        raise RuntimeError(u"Pacote baixado não contém a estrutura AutoBIM.tab esperada.")

    backups = _preservar_dados_locais()
    try:
        # Atualiza pastas principais substituindo arquivos antigos de codigo/botoes, preservando .git e _cache_rfa
        for item in os.listdir(src_root):
            if item == ".git":
                continue
            s_item = os.path.join(src_root, item)
            d_item = os.path.join(EXT_ROOT, item)
            if os.path.isdir(s_item):
                if item == "lib":
                    # Copia recursivamente preservando _cache_rfa e *_settings.json
                    _copiar_arvore_mesclando(s_item, d_item)
                else:
                    if os.path.isdir(d_item):
                        try:
                            shutil.rmtree(d_item)
                        except Exception:
                            _copiar_arvore_mesclando(s_item, d_item)
                            continue
                    shutil.copytree(s_item, d_item)
            else:
                shutil.copy2(s_item, d_item)
    finally:
        _restaurar_dados_locais(backups)

    # Atualiza cache remoto para nao re-avisar da mesma versao
    try:
        if os.path.isfile(REMOTE_CACHE_PATH):
            os.remove(REMOTE_CACHE_PATH)
    except Exception:
        pass
    return ler_version_local().get("version", u"atualizada")


def _copiar_arvore_mesclando(src, dst):
    if not os.path.isdir(dst):
        os.makedirs(dst)
    for name in os.listdir(src):
        if name in ("__pycache__", "_cache_rfa", ".git"):
            continue
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            _copiar_arvore_mesclando(s, d)
        else:
            if name.endswith("_settings.json") or name == "_AutoBIM_PeDireito_SP.txt":
                continue
            shutil.copy2(s, d)


# =============================================================================
# VERIFICACAO SILENCIOSA EM BACKGROUND (STARTUP)
# =============================================================================
def iniciar_checagem_background():
    """Chamado por startup.py ao abrir o Revit: checa GitHub sem bloquear o Revit."""
    if not HAS_UI:
        return
    prefs = carregar_prefs_updater()
    if not prefs.get("auto_check", True):
        return

    try:
        if os.path.isfile(REMOTE_CACHE_PATH):
            with codecs.open(REMOTE_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if (time.time() - float(cache.get("checked_at", 0.0))) < CHECK_INTERVAL_SEC:
                return
    except Exception:
        pass

    def _worker():
        try:
            res = buscar_info_remota(timeout_ms=2500)
            if res.get("ok"):
                with codecs.open(REMOTE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    try:
        t = Thread(ThreadStart(_worker))
        t.IsBackground = True
        t.Start()
    except Exception:
        pass


# =============================================================================
# INTERFACE WPF DE SOBRE / ATUALIZACAO
# =============================================================================
XAML_UPDATER = u"""
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="AutoBIM - Sobre e Atualizações"
        Width="535" SizeToContent="Height"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        Background="#F3F3F3" FontFamily="Segoe UI" FontSize="12">
    <StackPanel Margin="16">
        <Border Background="#E3EEF9" BorderBrush="#0078D4" BorderThickness="0,0,0,2"
                Padding="12,10" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock Text="AutoBIM — Produtividade para Revit (pyRevit)" FontWeight="Bold" FontSize="14" Foreground="#1F3A57"/>
                <TextBlock x:Name="lbl_ver_local" Margin="0,3,0,0" Foreground="#1F3A57" Text="Versão instalada: v1.0.0"/>
            </StackPanel>
        </Border>

        <Border Background="White" BorderBrush="#D0D7DE" BorderThickness="1" CornerRadius="4" Padding="12" Margin="0,0,0,10">
            <StackPanel>
                <TextBlock x:Name="lbl_status" FontWeight="SemiBold" TextWrapping="Wrap" Text="Verificando atualizações..."/>
                <TextBlock x:Name="lbl_repo_aviso" Visibility="Collapsed" Margin="0,6,0,0" Foreground="#9A6700" TextWrapping="Wrap"
                           Text="Nota: Esta pasta é um repositório Git local (.git detectado). Você pode atualizar via 'git pull' ou clicando em Atualizar."/>
                <Border Background="#F8FAFC" BorderBrush="#E2E8F0" BorderThickness="1" CornerRadius="3" Padding="8" Margin="0,8,0,0">
                    <ScrollViewer MaxHeight="135" VerticalScrollBarVisibility="Auto">
                        <TextBlock x:Name="tb_notas" TextWrapping="Wrap" FontSize="11" Foreground="#334155" Text="-"/>
                    </ScrollViewer>
                </Border>
            </StackPanel>
        </Border>

        <CheckBox x:Name="ck_auto_check" Content="Verificar atualizações automaticamente ao abrir o Revit" IsChecked="True" Margin="0,0,0,10"/>

        <Grid>
            <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <Button x:Name="btn_verificar" Grid.Column="0" Content="Verificar GitHub Agora" Width="150" Height="30"/>
            <Button x:Name="btn_ignorar" Grid.Column="2" Content="Ignorar Versão" Width="105" Height="30" Margin="0,0,8,0" Visibility="Collapsed"/>
            <Button x:Name="btn_atualizar" Grid.Column="3" Content="Atualizar Agora" Width="120" Height="30"
                    Background="#0078D4" Foreground="White" FontWeight="SemiBold" Margin="0,0,8,0" Visibility="Collapsed"/>
            <Button x:Name="btn_fechar" Grid.Column="4" Content="Fechar" Width="80" Height="30"/>
        </Grid>
    </StackPanel>
</Window>
"""


def _abrir_janela_updater(info_inicial=None, modo_aviso_novo=False):
    win = XamlReader.Parse(XAML_UPDATER)
    prefs = carregar_prefs_updater()
    loc = ler_version_local()

    lbl_ver = win.FindName("lbl_ver_local")
    lbl_status = win.FindName("lbl_status")
    lbl_aviso_git = win.FindName("lbl_repo_aviso")
    tb_notas = win.FindName("tb_notas")
    ck_auto = win.FindName("ck_auto_check")
    btn_verif = win.FindName("btn_verificar")
    btn_ign = win.FindName("btn_ignorar")
    btn_upd = win.FindName("btn_atualizar")
    btn_fechar = win.FindName("btn_fechar")

    lbl_ver.Text = u"Versão instalada: v{}  ({})".format(loc.get("version", "1.0.0"), loc.get("release_date", ""))
    ck_auto.IsChecked = bool(prefs.get("auto_check", True))
    if os.path.isdir(os.path.join(EXT_ROOT, ".git")):
        lbl_aviso_git.Visibility = WpfVisibility.Visible

    estado = {"info": info_inicial}

    def _aplicar_estado(info):
        estado["info"] = info
        if not info or not info.get("ok"):
            lbl_status.Text = u"Não foi possível conectar ao GitHub: {}".format(
                (info or {}).get("error") or u"verifique sua conexão."
            )
            tb_notas.Text = loc.get("notes") or u"Sem notas locais."
            btn_upd.Visibility = WpfVisibility.Collapsed
            btn_ign.Visibility = WpfVisibility.Collapsed
            btn_fechar.Visibility = WpfVisibility.Visible
            return

        if info.get("has_update"):
            lbl_status.Text = u"Nova versão disponível no GitHub: v{}  (atual: v{})".format(
                info["remote_version"], info["local_version"]
            )
            tb_notas.Text = info.get("notes") or u"Atualização disponível no repositório oficial."
            btn_upd.Visibility = WpfVisibility.Visible
            btn_ign.Visibility = WpfVisibility.Visible if modo_aviso_novo else WpfVisibility.Collapsed
            btn_fechar.Visibility = WpfVisibility.Visible
        else:
            lbl_status.Text = u"Você já está na versão mais recente (v{}).".format(info["local_version"])
            tb_notas.Text = info.get("notes") or loc.get("notes") or u"Nenhuma pendência."
            btn_upd.Visibility = WpfVisibility.Collapsed
            btn_ign.Visibility = WpfVisibility.Collapsed
            btn_fechar.Visibility = WpfVisibility.Visible

    def _ao_check(s, a):
        salvar_prefs_updater({"auto_check": bool(ck_auto.IsChecked)})

    def _ao_verif(s, a):
        lbl_status.Text = u"Consultando GitHub..."
        res = buscar_info_remota(timeout_ms=5000)
        if res.get("ok"):
            try:
                with codecs.open(REMOTE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        _aplicar_estado(res)

    def _ao_ignorar(s, a):
        info = estado.get("info")
        if info and info.get("remote_version"):
            salvar_prefs_updater({"skipped_version": info["remote_version"]})
        win.Close()

    def _ao_atualizar(s, a):
        info = estado.get("info")
        if not info or not info.get("zip_url"):
            return
        try:
            nova_v = aplicar_atualizacao_zip(info["zip_url"])
            win.Close()
            forms.alert(
                u"AutoBIM atualizado para v{} com sucesso!\n\nSuas preferências locais foram preservadas.\nClique em 'pyRevit → Reload' (ou reinicie o Revit) para carregar as novidades.".format(nova_v),
                title="AutoBIM Atualizado",
            )
        except Exception as ex:
            forms.alert(u"Erro ao baixar/instalar atualização:\n{}".format(ex), title="Erro na Atualização")

    def _ao_fechar(s, a):
        win.Close()

    ck_auto.Click += _ao_check
    btn_verif.Click += _ao_verif
    btn_ign.Click += _ao_ignorar
    btn_upd.Click += _ao_atualizar
    btn_fechar.Click += _ao_fechar

    if info_inicial is None:
        _ao_verif(None, None)
    else:
        _aplicar_estado(info_inicial)

    win.ShowDialog()


def notificar_se_houver_atualizacao():
    """Chamado por hooks/doc-opened.py na UI thread do Revit, no maximo 1x por sessao."""
    if not HAS_UI:
        return
    if os.environ.get("AUTOBIM_UPDATE_PROMPTED") == "1":
        return
    os.environ["AUTOBIM_UPDATE_PROMPTED"] = "1"

    prefs = carregar_prefs_updater()
    if not prefs.get("auto_check", True):
        return
    if not os.path.isfile(REMOTE_CACHE_PATH):
        return
    try:
        with codecs.open(REMOTE_CACHE_PATH, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        return

    if not info.get("ok") or not info.get("has_update"):
        return
    v_loc = ler_version_local().get("version", "1.0.0")
    if comparar_versoes(v_loc, info.get("remote_version", "")) <= 0:
        return
    if info.get("remote_version") == prefs.get("skipped_version"):
        return

    info["local_version"] = v_loc
    _abrir_janela_updater(info_inicial=info, modo_aviso_novo=True)


def run_sobre_atualizar():
    if not HAS_UI:
        return
    _abrir_janela_updater(info_inicial=None, modo_aviso_novo=False)
