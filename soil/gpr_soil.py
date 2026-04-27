"""
=============================================================================
  GPR FMCW — CARACTERIZACIÓN DE SUELOS AGRÍCOLAS
=============================================================================

  OBJETIVO
  ────────────────────────────────────────────────────────────────────────────
  Usar el radar FMCW para obtener información cuantitativa del suelo:
    · Permitividad relativa (ε) por horizonte
    · Humedad volumétrica (θ) via modelo de Topp (1980)
    · Conductividad eléctrica aparente (σ) via atenuación
    · Profundidad de horizontes pedológicos (A, B, C)
    · Detección de napa freática
    · Compactación / variación lateral de θ en el perfil

  DIFERENCIA RESPECTO A LA DETECCIÓN DE TARGETS
  ────────────────────────────────────────────────────────────────────────────
  Sin targets, los reflectores son las INTERFACES entre horizontes de suelo.
  Cada cambio brusco de ε produce una reflexión parcial.
  El coeficiente de reflexión en la interfaz entre dos capas:

      R = (sqrt(ε2) - sqrt(ε1)) / (sqrt(ε2) + sqrt(ε1))

  Un R pequeño (~0.05-0.20) es lo que produce cada interfaz de suelo real.
  El B-scan muestra bandas horizontales en lugar de hipérbolas.

  EXTRACCIÓN DE ε POR HORIZONTE
  ────────────────────────────────────────────────────────────────────────────
  Método: tiempo de viaje vertical (TWTT) de la reflexión de cada interfaz.

    τ_n = profundidad_n / v_n   [tiempo de ida]
    f_beat_n = (BW/T) · 2·τ_n

  Si f_beat_n y la profundidad real d_n son conocidas:
    v_n = 2 · d_n / (f_beat_n · T / BW)
    ε_n = (1/v_n)²   [en unidades MEEP donde c=1]

  En la práctica: cada interfaz produce un pico en el espectro beat.
  Identificando ese pico podemos invertir la permitividad de la capa.

  MODELO DE TOPP (1980)
  ────────────────────────────────────────────────────────────────────────────
  La relación empírica más usada en agricultura entre ε y θ:

      θ = -5.3·10⁻² + 2.92·10⁻²·ε - 5.5·10⁻⁴·ε² + 4.3·10⁻⁶·ε³

  Válido para: suelos minerales, sin sal, 0 < θ < 0.55, 1 MHz – 1 GHz.
  Error típico: ±0.013 m³/m³ para la mayoría de suelos minerales.

  ESTIMACIÓN DE CONDUCTIVIDAD DESDE LA ATENUACIÓN
  ────────────────────────────────────────────────────────────────────────────
  La amplitud del espectro beat decae con la profundidad como:
      A(d) ∝ exp(−α·d)
  donde α [dB/m] depende de f, ε y σ:
      α ≈ ω · sqrt(μ₀·ε₀·ε/2 · (sqrt(1 + (σ/ωε₀ε)²) − 1))

  Midiendo la pendiente de log(A) vs d se puede estimar σ de cada capa.

  ESCENARIOS AGRÍCOLAS IMPLEMENTADOS
  ────────────────────────────────────────────────────────────────────────────
  1. suelo_seco          — Suelo uniforme en estrés hídrico (θ~5%)
  2. suelo_humedo        — Suelo homogéneo post-lluvia (θ~40%)
  3. horizonte_abc       — Perfil clásico horizonte A-B-C con gradiente
  4. napa_freatica       — Napa a 1m de profundidad
  5. gradiente_humedad   — Gradiente lateral de humedad (zona anegada a la derecha)
  6. suelo_compactado    — Capa compactada (piso de arado) a 30 cm
  7. variabilidad_campo  — Perfil heterogéneo realista de campo agrícola

  DEPENDENCIAS: meep, numpy, scipy, matplotlib
=============================================================================
"""

import meep as mp
import numpy as np
from scipy.signal import windows, butter, filtfilt, find_peaks
from scipy.optimize import brentq
from scipy.ndimage import uniform_filter1d
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import warnings
import time

mp.verbosity(0)
# RankWarning se movió a numpy.exceptions en NumPy 2.0+; suprimir por mensaje
warnings.filterwarnings('ignore', message='.*Polyfit.*')


# =============================================================================
# BLOQUE 0 — PARÁMETROS GLOBALES
# =============================================================================

SX         = 12.0
SY         = 8.0
DPML       = 1.0
RESOLUTION = 20

CHIRP_F1   = 0.50    # 1 GHz en u.MEEP (a=0.15m)
CHIRP_F2   = 1.00    # 2 GHz en u.MEEP
T_MAX      = 40.0
A_MEEP     = 0.15    # metros por unidad MEEP
C_REAL     = 3e8

ANTENNA_SEP    = 0.5
ANTENNA_LENGTH = 0.0
SHIELD_WIDTH   = 1.0
SHIELD_HEIGHT  = 0.12
SHIELD_GAP     = 0.06
HORN_WALL_T    = 0.05
HORN_BOT_Y     = 0.2
Y_ANTENNA      = HORN_BOT_Y + 0.45

X_START    = -4.0
X_END      =  4.0
NUM_TRACES =  60     # más trazas que antes: necesitamos resolución lateral
DT_RECORD  =  0.05

LPF_CUTOFF_MEEP = 0.30
LPF_ORDER       = 4
ZERO_PAD        = 8
GAIN_ALPHA      = 0.05


# =============================================================================
# BLOQUE 1 — MODELO FÍSICO DE SUELOS (Topp 1980 + atenuación)
# =============================================================================

def topp_eps_to_theta(eps):
    """
    Humedad volumétrica (m³/m³) desde permitividad relativa.
    Modelo de Topp, Davis & Annan (1980) — estándar en agricultura.
    Válido para suelos minerales no salinos, 1 MHz – 1 GHz.
    Error típico: ±0.013 m³/m³.
    """
    theta = -5.3e-2 + 2.92e-2 * eps - 5.5e-4 * eps**2 + 4.3e-6 * eps**3
    return float(np.clip(theta, 0.0, 0.65))


def topp_theta_to_eps(theta):
    """
    Permitividad relativa desde humedad volumétrica (inversa del modelo de Topp).
    """
    return 3.03 - 9.3*theta + 146*theta**2 - 76.7*theta**3


def attenuation_dBm(eps_r, sigma_Spm, f_hz=1.5e9):
    """
    Coeficiente de atenuación [dB/m] para un suelo con parámetros (ε, σ).

    Fórmula exacta de la constante de atenuación EM:
        α = ω · sqrt( μ₀·ε₀·ε/2 · (sqrt(1 + (σ/(ω·ε₀·ε))²) − 1) )

    Parámetros
    ----------
    eps_r    : permitividad relativa [-]
    sigma_Spm: conductividad eléctrica [S/m]
    f_hz     : frecuencia central [Hz] (default 1.5 GHz)
    """
    eps0  = 8.854e-12
    mu0   = 4 * np.pi * 1e-7
    omega = 2 * np.pi * f_hz
    eps   = eps_r * eps0
    loss  = sigma_Spm / (omega * eps)
    alpha = omega * np.sqrt(mu0 * eps / 2.0 * (np.sqrt(1 + loss**2) - 1))
    return 8.686 * alpha   # Np/m → dB/m


def skin_depth_m(eps_r, sigma_Spm, f_hz=1.5e9):
    """Profundidad de piel en metros (donde la señal cae a 1/e ≈ -8.7 dB)."""
    a = attenuation_dBm(eps_r, sigma_Spm, f_hz)
    return 8.686 / a if a > 1e-3 else 999.0


def classify_soil_texture(eps_r, sigma_Spm):
    """
    Clasificación cualitativa de textura de suelo a partir de ε y σ.
    Devuelve una cadena descriptiva. Heurística basada en rangos típicos
    de la literatura de GPR agrícola.
    """
    theta = topp_eps_to_theta(eps_r)
    if eps_r < 4.5:
        base = "Arena"
    elif eps_r < 8:
        base = "Franco arenoso"
    elif eps_r < 13:
        base = "Franco"
    elif eps_r < 20:
        base = "Franco arcilloso"
    else:
        base = "Arcilla"

    if theta < 0.05:
        estado = "muy seco"
    elif theta < 0.15:
        estado = "seco"
    elif theta < 0.25:
        estado = "capacidad campo"
    elif theta < 0.35:
        estado = "húmedo"
    elif theta < 0.45:
        estado = "muy húmedo"
    else:
        estado = "saturado"

    return f"{base} — {estado}"


def layer_info_str(L):
    """Genera una cadena de diagnóstico completo para una capa de suelo."""
    eps   = L['eps']
    sigma = L['sigma']
    theta = topp_eps_to_theta(eps)
    alpha = attenuation_dBm(eps, sigma)
    skin  = skin_depth_m(eps, sigma)
    text  = classify_soil_texture(eps, sigma)
    return (
        f"{L['name']}\n"
        f"  ε={eps:.1f}  σ={sigma:.4f} S/m\n"
        f"  θ={theta:.3f} ({theta*100:.1f}%)\n"
        f"  α={alpha:.1f} dB/m  →  pen.≈{skin:.2f}m\n"
        f"  Textura: {text}"
    )


# =============================================================================
# BLOQUE 2 — ESCENARIOS AGRÍCOLAS (sin targets)
# =============================================================================

def scenario_suelo_seco():
    """
    Suelo uniforme en condición de estrés hídrico.
    θ ≈ 5-8%. Situación: verano seco, sin riego, tierra de labranza.
    Señal penetra varios metros, las interfaces son débiles (poco contraste).
    """
    layers = [
        dict(y_top=0.0,  y_bot=-0.3, eps=3.5, sigma=0.001,
             name="Arado seco  (0–30 cm)", color="#F5E6C8"),
        dict(y_top=-0.3, y_bot=-0.8, eps=4.0, sigma=0.003,
             name="Subsuelo seco  (30–80 cm)", color="#E8D5A3"),
        dict(y_top=-0.8, y_bot=-4.0, eps=4.5, sigma=0.005,
             name="Horizonte C seco  (>80 cm)", color="#C8A882"),
    ]
    return layers, "Suelo seco — estrés hídrico"


def scenario_suelo_humedo():
    """
    Suelo homogéneo post-lluvia intensa.
    θ ≈ 35-45%. Situación: 24 hs después de lluvia de 50 mm.
    Alta atenuación: la señal penetra poco más de 50 cm.
    """
    layers = [
        dict(y_top=0.0,  y_bot=-0.2, eps=28.0, sigma=0.12,
             name="Superficie saturada  (0–20 cm)", color="#6B8E6B"),
        dict(y_top=-0.2, y_bot=-0.6, eps=20.0, sigma=0.08,
             name="Zona de mojado  (20–60 cm)", color="#8B9E6B"),
        dict(y_top=-0.6, y_bot=-4.0, eps=8.0,  sigma=0.015,
             name="Subsuelo no saturado  (>60 cm)", color="#C8A882"),
    ]
    return layers, "Suelo húmedo — post-lluvia"


def scenario_horizonte_abc():
    """
    Perfil pedológico clásico con horizontes A, B y C diferenciados.
    Situación: suelo agrícola maduro, sin riego reciente.
    Útil para mapear profundidad de cada horizonte.
    """
    layers = [
        dict(y_top=0.0,  y_bot=-0.3, eps=10.0, sigma=0.025,
             name="Horizonte A  (0–30 cm)", color="#5C4033"),
        dict(y_top=-0.3, y_bot=-0.7, eps=7.0,  sigma=0.015,
             name="Horizonte B  (30–70 cm)", color="#8B6914"),
        dict(y_top=-0.7, y_bot=-1.5, eps=4.5,  sigma=0.006,
             name="Horizonte C  (70–150 cm)", color="#C8A882"),
        dict(y_top=-1.5, y_bot=-4.0, eps=14.0, sigma=0.04,
             name="Roca alterada  (>150 cm)", color="#7A5C3A"),
    ]
    return layers, "Perfil A-B-C — pedología clásica"


def scenario_napa_freatica():
    """
    Napa freática a aproximadamente 1 m de profundidad.
    La interfaz suelo seco / suelo saturado produce una reflexión fuerte
    (alto contraste de ε: de ~6 a ~30). Escenario muy relevante para
    manejo de riego y drenaje.
    """
    layers = [
        dict(y_top=0.0,  y_bot=-0.4, eps=5.0,  sigma=0.008,
             name="Zona vadosa seca  (0–40 cm)", color="#DEB887"),
        dict(y_top=-0.4, y_bot=-1.0, eps=10.0, sigma=0.025,
             name="Franja capilar  (40–100 cm)", color="#A08040"),
        dict(y_top=-1.0, y_bot=-4.0, eps=30.0, sigma=0.15,
             name="Zona saturada — napa  (>100 cm)", color="#5C7A9E"),
    ]
    return layers, "Napa freática a ~1 m"


def scenario_gradiente_humedad():
    """
    Gradiente lateral de humedad: zona seca a la izquierda, zona anegada
    a la derecha (simulado mediante variación de ε en cada traza).
    Caso real: bordes de un lote irrigado, bajos topográficos.

    NOTA: este escenario usa capas con ε variable lateralmente.
    La variación se simula generando un escenario por traza
    con ε interpolado entre los valores seco y húmedo.
    """
    # Capa superficial: varía de seco (ε=4) a húmedo (ε=22) de izq a der
    # Capa profunda: relativamente uniforme
    layers = [
        dict(y_top=0.0,  y_bot=-0.5, eps=4.0,  sigma=0.005,
             name="Sup. seca (lado izq)  — varía lateralmente",
             color="#E8D5A3",
             eps_right=22.0, sigma_right=0.09),   # ← seco a izq, húmedo a der
        dict(y_top=-0.5, y_bot=-4.0, eps=6.0,  sigma=0.01,
             name="Subsuelo uniforme  (>50 cm)", color="#C8A882"),
    ]
    return layers, "Gradiente lateral de humedad"


def scenario_suelo_compactado():
    """
    Capa compactada (piso de arado) a ~30 cm de profundidad.
    La compactación aumenta la densidad y disminuye la porosidad,
    lo que sube ε ligeramente y sube σ (más contacto entre partículas).
    Escenario muy relevante para manejo agronómico: el piso de arado
    impide el drenaje y el crecimiento radicular.
    """
    layers = [
        dict(y_top=0.0,  y_bot=-0.3, eps=6.0,  sigma=0.012,
             name="Capa arada  (0–30 cm)", color="#DEB887"),
        dict(y_top=-0.3, y_bot=-0.4, eps=18.0, sigma=0.08,
             name="PISO DE ARADO  (30–40 cm) ← compactado", color="#5C4033"),
        dict(y_top=-0.4, y_bot=-4.0, eps=7.0,  sigma=0.015,
             name="Subsuelo  (>40 cm)", color="#C8A882"),
    ]
    return layers, "Piso de arado — capa compactada a 30 cm"


def scenario_variabilidad_campo():
    """
    Perfil heterogéneo realista de campo agrícola templado.
    Combina variación vertical (horizontes) con ligera variación lateral
    (simulada con escenario estático promedio). Escenario más complejo.
    """
    layers = [
        dict(y_top=0.0,  y_bot=-0.25, eps=12.0, sigma=0.030,
             name="Horizonte A húmedo  (0–25 cm)", color="#4A3728"),
        dict(y_top=-0.25, y_bot=-0.5, eps=8.0,  sigma=0.018,
             name="Horizonte A profundo  (25–50 cm)", color="#6B4F33"),
        dict(y_top=-0.5, y_bot=-0.9, eps=5.5,  sigma=0.010,
             name="Horizonte B superior  (50–90 cm)", color="#9B7845"),
        dict(y_top=-0.9, y_bot=-1.5, eps=4.0,  sigma=0.006,
             name="Horizonte B-C  (90–150 cm)", color="#C8A882"),
        dict(y_top=-1.5, y_bot=-4.0, eps=20.0, sigma=0.06,
             name="Roca madre húmeda  (>150 cm)", color="#6B5A4A"),
    ]
    return layers, "Perfil campo agrícola — variabilidad realista"


# =============================================================================
# BLOQUE 3 — GEOMETRÍA MEEP (sin targets)
# =============================================================================

def build_geometry_soil(layers):
    """
    Construye objetos MEEP para suelo estratificado (sin targets).
    MEEP aplica "último gana": el orden importa, pero sin targets
    no hay sobreposición de objetos.
    """
    objs = []
    for L in layers:
        thickness = abs(L['y_top'] - L['y_bot'])
        center_y  = (L['y_top'] + L['y_bot']) / 2.0
        objs.append(mp.Block(
            center   = mp.Vector3(0, center_y, 0),
            size     = mp.Vector3(mp.inf, thickness, mp.inf),
            material = mp.Medium(epsilon=L['eps'], D_conductivity=L['sigma'])
        ))
    return objs


def build_geometry_soil_xvarying(layers, x_frac):
    """
    Construye geometría con ε variable lateralmente para el escenario
    de gradiente de humedad.

    x_frac : float en [0,1] — posición normalizada (0=izquierda, 1=derecha)
    """
    objs = []
    for L in layers:
        thickness = abs(L['y_top'] - L['y_bot'])
        center_y  = (L['y_top'] + L['y_bot']) / 2.0
        if 'eps_right' in L:
            eps_val   = L['eps']   + x_frac * (L['eps_right']   - L['eps'])
            # sigma_right es obligatorio cuando eps_right está definido
            sigma_val = L['sigma'] + x_frac * (L.get('sigma_right', L['sigma']) - L['sigma'])
        else:
            eps_val   = L['eps']
            sigma_val = L['sigma']
        objs.append(mp.Block(
            center   = mp.Vector3(0, center_y, 0),
            size     = mp.Vector3(mp.inf, thickness, mp.inf),
            material = mp.Medium(epsilon=eps_val, D_conductivity=sigma_val)
        ))
    return objs


def build_shield(x_pos):
    """
    Bocina metálica: pared horizontal + 3 paredes verticales.
    Igual que en chirp_v7.py.
    """
    objects = []
    top_cy = Y_ANTENNA + SHIELD_GAP + SHIELD_HEIGHT / 2
    objects.append(mp.Block(
        center   = mp.Vector3(x_pos, top_cy, 0),
        size     = mp.Vector3(SHIELD_WIDTH, SHIELD_HEIGHT, mp.inf),
        material = mp.metal
    ))
    wall_top    = Y_ANTENNA + SHIELD_GAP
    wall_height = wall_top - HORN_BOT_Y
    wall_cy     = (wall_top + HORN_BOT_Y) / 2
    for x_wall in [x_pos - SHIELD_WIDTH/2, x_pos, x_pos + SHIELD_WIDTH/2]:
        objects.append(mp.Block(
            center   = mp.Vector3(x_wall, wall_cy, 0),
            size     = mp.Vector3(HORN_WALL_T, wall_height, mp.inf),
            material = mp.metal
        ))
    return objects


# =============================================================================
# BLOQUE 4 — SEÑAL CHIRP (idéntica a chirp_v7.py)
# =============================================================================

def chirp_func(t):
    """Chirp lineal con envolvente Tukey (10%)."""
    if t <= 0.0 or t >= T_MAX:
        return 0.0
    edge = 0.10 * T_MAX
    if t < edge:
        amp = 0.5 * (1.0 - np.cos(np.pi * t / edge))
    elif t > T_MAX - edge:
        amp = 0.5 * (1.0 - np.cos(np.pi * (T_MAX - t) / edge))
    else:
        amp = 1.0
    rate  = (CHIRP_F2 - CHIRP_F1) / T_MAX
    phase = 2.0 * np.pi * (CHIRP_F1 * t + 0.5 * rate * t * t)
    return amp * np.sin(phase)


def make_reference_chirp(n_samples):
    """Señal de referencia Tx discreta (idéntica a la fuente MEEP)."""
    return np.array([chirp_func(i * DT_RECORD) for i in range(n_samples)])


# =============================================================================
# BLOQUE 5 — SIMULACIÓN FDTD
# =============================================================================

def simulate_ascan_soil(x_pos, layers, x_frac=None):
    """
    Simula una traza con señal chirp en el escenario de suelo (sin targets).

    x_frac : float [0,1] o None. Si se provee, usa geometría con variación
             lateral de ε (para el escenario de gradiente de humedad).
    """
    tx_pos   = mp.Vector3(x_pos - ANTENNA_SEP/2, Y_ANTENNA, 0)
    rx_pos   = mp.Vector3(x_pos + ANTENNA_SEP/2, Y_ANTENNA, 0)
    f_center = (CHIRP_F1 + CHIRP_F2) / 2.0
    f_width  = CHIRP_F2 - CHIRP_F1

    source = mp.Source(
        src = mp.CustomSource(
            src_func         = chirp_func,
            start_time       = 0.0,
            end_time         = T_MAX,
            center_frequency = f_center,
            fwidth           = f_width
        ),
        component = mp.Ez,
        center    = tx_pos,
        size      = mp.Vector3(ANTENNA_LENGTH, 0, 0)
    )

    if x_frac is not None:
        geo = build_geometry_soil_xvarying(layers, x_frac)
    else:
        geo = build_geometry_soil(layers)

    geometry = geo + build_shield(x_pos)

    sim = mp.Simulation(
        cell_size       = mp.Vector3(SX, SY, 0),
        boundary_layers = [mp.PML(DPML)],
        geometry        = geometry,
        sources         = [source],
        resolution      = RESOLUTION
    )

    rx_samples = []
    def _record(sim_obj):
        rx_samples.append(np.real(sim_obj.get_field_point(mp.Ez, rx_pos)))

    sim.run(mp.at_every(DT_RECORD, _record), until=T_MAX)
    return np.array(rx_samples)


# =============================================================================
# BLOQUE 6 — PROCESAMIENTO FMCW (idéntico a chirp_v7.py)
# =============================================================================

def apply_mixer(rx, tx_ref):
    return rx * tx_ref


def apply_lpf(if_signal):
    fs_meep = 1.0 / DT_RECORD
    nyq     = fs_meep / 2.0
    wn      = min(LPF_CUTOFF_MEEP / nyq, 0.99)
    b, a    = butter(LPF_ORDER, wn, btype='low')
    return filtfilt(b, a, if_signal)


def compute_beat_spectrum(if_lpf, n_samples, return_complex=False):
    Nfft          = n_samples * ZERO_PAD
    win           = windows.hann(len(if_lpf))
    if_win        = if_lpf[:n_samples] * win[:len(if_lpf)]
    spectrum_full = np.fft.fft(if_win, n=Nfft)
    freqs_full    = np.fft.fftfreq(Nfft, d=DT_RECORD)
    half          = Nfft // 2
    freqs         = freqs_full[:half]
    if return_complex:
        return spectrum_full[:half], freqs
    return np.abs(spectrum_full[:half]), freqs


def beat_freq_to_depth_meep(f_beat_meep, eps_r=1.0):
    v_meep     = 1.0 / np.sqrt(eps_r)
    BW_meep    = CHIRP_F2 - CHIRP_F1
    return f_beat_meep * v_meep * T_MAX / (2.0 * BW_meep)


def process_bscan_fmcw(bscan_rx, tx_ref, layers):
    """
    Pipeline FMCW completo: mixer → LPF → FFT → background removal → ganancia.
    Retorna B (módulo), freqs, B_raw_if, S_complex (con fase para migración).
    """
    n_traces, N = bscan_rx.shape
    Nfft_half   = N * ZERO_PAD // 2

    spectra         = np.zeros((n_traces, Nfft_half))
    spectra_complex = np.zeros((n_traces, Nfft_half), dtype=complex)
    if_signals      = np.zeros((n_traces, N))

    for i in range(n_traces):
        if_raw = apply_mixer(bscan_rx[i], tx_ref)
        if_lpf = apply_lpf(if_raw)
        if_signals[i] = if_lpf
        spec_c, freqs = compute_beat_spectrum(if_lpf, N, return_complex=True)
        spectra[i]         = np.abs(spec_c)
        spectra_complex[i] = spec_c

    B = spectra.T
    B = B - np.median(B, axis=1, keepdims=True)

    idx_arr = np.arange(Nfft_half, dtype=float) / Nfft_half
    gain    = np.exp(GAIN_ALPHA * 10.0 * idx_arr)[:, np.newaxis]
    B       = B * gain

    S_complex = spectra_complex.T
    B_raw_if  = if_signals.T
    return B, freqs, B_raw_if, S_complex


def useful_freq_mask(freqs, layers):
    """Máscara de frecuencias útiles basada en la profundidad máxima de la celda."""
    BW_meep    = CHIRP_F2 - CHIRP_F1
    eps_max    = max(L['eps'] for L in layers)
    v_min      = 1.0 / np.sqrt(eps_max)
    f_beat_max = (BW_meep / T_MAX) * (2.0 * (SY / 2.0) / v_min) * 1.20
    mask       = (freqs >= 0) & (freqs <= f_beat_max)
    if mask.sum() < 10:
        mask = np.ones(len(freqs), dtype=bool)
    return mask


# =============================================================================
# BLOQUE 7 — INVERSIÓN DIELÉCTRICA
# =============================================================================

def detect_layer_interfaces(mean_spectrum, depth_meep_arr, layers,
                              prominence_threshold=0.05):
    """
    Detecta los picos en el espectro beat promedio que corresponden a
    las interfaces entre horizontes de suelo.

    En un suelo sin targets, el espectro beat muestra picos relativamente
    débiles en las profundidades de cada interfaz. Cada pico corresponde
    al tiempo de viaje de ida y vuelta hasta esa discontinuidad de ε.

    Método: búsqueda de máximos locales por encima del umbral.

    Parámetros
    ----------
    mean_spectrum      : array (N,) — espectro beat promedio sobre todas las trazas
    depth_meep_arr     : array (N,) — eje de profundidad aparente [u.MEEP]
    layers             : lista de capas (para calcular profundidades teóricas)
    prominence_threshold: fracción del máximo para considerar un pico

    Retorna
    -------
    peaks_depth : list de profundidades detectadas [u.MEEP]
    peaks_amp   : list de amplitudes en esos picos
    """
    # Normalizar
    spec_norm = mean_spectrum / (np.max(mean_spectrum) + 1e-12)

    # Calcular distancia mínima entre picos basada en la resolución teórica
    # ΔR = v / (2·BW) en u.MEEP; convertido a índices
    v_avg   = 1.0 / np.sqrt(layers[0]['eps'])
    BW_meep = CHIRP_F2 - CHIRP_F1
    delta_d = v_avg / (2.0 * BW_meep)   # resolución mínima [u.MEEP]
    dd      = depth_meep_arr[1] - depth_meep_arr[0] if len(depth_meep_arr) > 1 else 0.01
    min_dist_idx = max(3, int(delta_d / dd * 0.5))

    peaks_idx, props = find_peaks(
        spec_norm,
        height=prominence_threshold,
        distance=min_dist_idx
    )

    peaks_depth = [depth_meep_arr[i] for i in peaks_idx]
    peaks_amp   = [mean_spectrum[i]  for i in peaks_idx]
    return peaks_depth, peaks_amp


def invert_eps_from_spectrum(mean_spectrum, freqs, layers):
    """
    Inversión de la permitividad a partir de las posiciones espectrales
    de las reflexiones de interfaz.

    Dado un pico en f_beat = f_n, y sabiendo que este corresponde a la
    interfaz a profundidad d_n, la velocidad en las capas superiores es:

        τ_cumulative = 2 · Σ(d_k / v_k)   para k = 1..n
        f_n = (BW / T) · τ_cumulative

    Para la primera interfaz (sola capa superficial):
        v_1 = 2 · d_1 / (f_1 · T / BW)
        ε_1 = 1 / v_1²

    Para la segunda interfaz se necesita descontar el tiempo ya acumulado
    en la primera capa, etc. (análisis de velocidades por capa).

    En la práctica, con múltiples capas la inversión se hace iterativamente.

    Parámetros
    ----------
    mean_spectrum : array — espectro beat promedio
    freqs         : array — eje de frecuencia beat [u.MEEP]
    layers        : lista — capas reales (para comparación)

    Retorna
    -------
    results : lista de dicts con profundidad_meep, eps_inv, theta_inv por interfaz
    """
    BW_meep = CHIRP_F2 - CHIRP_F1
    eps_ref = layers[0]['eps']
    depth_arr = np.array([beat_freq_to_depth_meep(f, eps_ref) for f in freqs])

    mask = useful_freq_mask(freqs, layers)
    depth_u = depth_arr[mask]
    spec_u  = mean_spectrum[mask]

    peaks_depth, peaks_amp = detect_layer_interfaces(spec_u, depth_u, layers)

    results = []
    t_cumulative = 0.0  # tiempo acumulado [u.MEEP]
    d_cumulative = 0.0  # profundidad acumulada [u.MEEP]

    for idx, (d_peak, amp) in enumerate(zip(peaks_depth, peaks_amp)):
        # Tiempo de viaje de ida y vuelta hasta este pico
        f_beat_peak  = (BW_meep / T_MAX) * (2.0 * d_peak / (1.0 / np.sqrt(eps_ref)))

        # Para la primera capa, la velocidad se obtiene directamente
        if idx == 0:
            d_layer = d_peak  # profundidad de la interfaz = grosor de la capa 1
            if d_layer > 0:
                tau_layer = d_layer / (1.0 / np.sqrt(eps_ref))
                # Invertir: v_layer = d_layer / tau_layer
                v_inv = d_layer / tau_layer
                eps_inv = (1.0 / v_inv) ** 2
            else:
                eps_inv = eps_ref
            t_cumulative += 2.0 * d_layer * np.sqrt(eps_ref)
        else:
            # Capas subsiguientes: descontar tiempo de capas superiores
            d_layer = d_peak - d_cumulative
            if d_layer > 0.05:
                # Usar los parámetros de la capa anterior para estimar τ_capa
                # Simplificación: v_layer ≈ d_layer / (tau_total - tau_cumulative)
                eps_prev  = results[-1]['eps_inv'] if results else eps_ref
                tau_extra = t_cumulative
                tau_total_approx = 2.0 * d_peak / (1.0 / np.sqrt(eps_ref))
                tau_layer = (tau_total_approx - tau_extra) / 2.0
                if tau_layer > 1e-6:
                    v_inv   = d_layer / tau_layer
                    eps_inv = (1.0 / v_inv) ** 2
                else:
                    eps_inv = eps_ref
                t_cumulative += 2.0 * d_layer * np.sqrt(max(eps_inv, 1.0))
            else:
                eps_inv = results[-1]['eps_inv'] if results else eps_ref

        d_cumulative = d_peak
        theta_inv    = topp_eps_to_theta(eps_inv)

        results.append({
            'depth_meep':  d_peak,
            'depth_m':     d_peak * A_MEEP,
            'eps_inv':     eps_inv,
            'theta_inv':   theta_inv,
            'amplitude':   amp,
            'texture':     classify_soil_texture(eps_inv, 0.01),  # σ desconocido aquí
        })

    return results


def estimate_attenuation_profile(B_u, depth_u, x_positions):
    """
    Estima el perfil de atenuación α(d) [dB/u.MEEP] ajustando una exponencial
    a la caída de amplitud del espectro beat con la profundidad.

    En un suelo sin targets la energía debería caer suavemente con d.
    La pendiente de log(RMS_amplitud) vs d da α.
    A partir de α y ε conocido, se puede estimar σ.

    Parámetros
    ----------
    B_u         : array (Nd, Nx) — B-scan en región útil (después de background removal)
    depth_u     : array (Nd,)    — eje de profundidad [u.MEEP]
    x_positions : array (Nx,)    — posiciones X

    Retorna
    -------
    alpha_meep     : float — coeficiente de atenuación [dB / u.MEEP]
    alpha_dBm      : float — coeficiente convertido a [dB / metro]
    rms_profile    : array (Nd,) — amplitud RMS vs profundidad
    fit_profile    : array (Nd,) — perfil ajustado
    """
    # Amplitud RMS a lo largo de x para cada profundidad
    rms_profile = np.sqrt(np.mean(B_u**2, axis=1)) + 1e-12

    # Ajuste de ley exponencial: A(d) = A0 * exp(-alpha * d)
    # En log: log(A) = log(A0) - alpha * d
    log_rms = np.log(rms_profile + 1e-12)

    # Usar solo la parte donde la señal tiene contenido real (primer 60%)
    n_fit = max(10, int(0.6 * len(depth_u)))
    d_fit   = depth_u[:n_fit]
    lA_fit  = log_rms[:n_fit]

    try:
        coeffs     = np.polyfit(d_fit, lA_fit, 1)
        alpha_meep = max(0.0, -coeffs[0])  # positivo = atenuación; clamp en 0
        alpha_dBm  = alpha_meep / A_MEEP
        fit_profile = np.exp(np.polyval(coeffs, depth_u))
    except Exception:
        alpha_meep  = 0.0
        alpha_dBm   = 0.0
        coeffs      = [0.0, np.log(rms_profile[0] + 1e-12)]
        fit_profile = np.exp(np.polyval(coeffs, depth_u))

    return alpha_meep, alpha_dBm, rms_profile, fit_profile


def estimate_sigma_from_alpha(alpha_dBm, eps_r, f_hz=1.5e9):
    """
    Estima la conductividad eléctrica σ [S/m] a partir del coeficiente
    de atenuación medido α [dB/m] y la permitividad conocida ε_r.

    Invierte numéricamente la fórmula de atenuación EM.
    """
    if alpha_dBm < 0.01:
        # Señal sin atenuación apreciable → suelo muy seco
        return 0.0005   # S/m conservador para arena seca

    def residual(sigma):
        return attenuation_dBm(eps_r, sigma, f_hz) - alpha_dBm

    try:
        # Verificar que el intervalo [1e-5, 5.0] contiene un cambio de signo
        fa = residual(1e-5)
        fb = residual(5.0)
        if fa * fb > 0:
            # No hay cambio de signo — extrapolar desde el extremo más cercano
            sigma_est = 1e-5 if abs(fa) < abs(fb) else 5.0
        else:
            sigma_est = brentq(residual, 1e-5, 5.0, xtol=1e-5)
    except Exception:
        sigma_est = 0.001   # fallback conservador

    return sigma_est


# =============================================================================
# BLOQUE 8 — VISUALIZACIÓN
# =============================================================================

def plot_geometry_soil(layers, scenario_name, x_ref=0.0):
    """
    Visualiza la geometría del escenario de suelo.
    Muestra las capas con sus propiedades físicas y la bocina.
    """
    print("Generando visualización de geometría...")

    geo = build_geometry_soil(layers) + build_shield(x_ref)
    sim = mp.Simulation(
        cell_size       = mp.Vector3(SX, SY, 0),
        geometry        = geo,
        resolution      = RESOLUTION,
        boundary_layers = [mp.PML(DPML)]
    )

    layer_colors = [L['color'] for L in layers]
    cmap_gpr = mcolors.LinearSegmentedColormap.from_list(
        "soil", ["#E3F2FD"] + layer_colors)

    fig, (ax_geo, ax_info) = plt.subplots(1, 2, figsize=(16, 7),
                                           gridspec_kw={'width_ratios': [3, 1]})

    sim.plot2D(ax=ax_geo, plot_eps_indices=True,
               eps_parameters={
                   'cmap': cmap_gpr, 'alpha': 1.0,
                   'vmin': 1.0, 'vmax': max(L['eps'] for L in layers)
               })

    for L in layers:
        ax_geo.axhline(L['y_top'], color='black', linestyle='--',
                       linewidth=1.2, alpha=0.5)

    ax_geo.text(-SX/2 + 0.2, 1.6, "AIRE  (ε=1)",
                color='#0277BD', fontsize=9, fontweight='bold')
    for L in layers:
        cy = (L['y_top'] + L['y_bot']) / 2
        theta = topp_eps_to_theta(L['eps'])
        ax_geo.text(-SX/2 + 0.2, cy,
                    f"{L['name'][:25]}\nε={L['eps']:.0f}  θ={theta:.2f}",
                    color='white', fontsize=7, fontweight='bold',
                    bbox=dict(facecolor='black', alpha=0.5, boxstyle='round,pad=0.2'))

    # Bocina
    tx_x = x_ref - ANTENNA_SEP / 2
    rx_x = x_ref + ANTENNA_SEP / 2
    ax_geo.plot(tx_x, Y_ANTENNA, 'v', ms=12, color='#29B6F6',
                markeredgecolor='white', zorder=7, label='Tx')
    ax_geo.plot(rx_x, Y_ANTENNA, '^', ms=12, color='#EF5350',
                markeredgecolor='white', zorder=7, label='Rx')
    sy_bot  = Y_ANTENNA + SHIELD_GAP
    horn_kw = dict(linewidth=1.5, edgecolor='#37474F', facecolor='#607D8B', alpha=0.65)
    ax_geo.add_patch(mpatches.FancyBboxPatch(
        (x_ref - SHIELD_WIDTH/2, sy_bot), SHIELD_WIDTH, SHIELD_HEIGHT,
        boxstyle="square,pad=0", label='Bocina', **horn_kw))
    wall_top    = Y_ANTENNA + SHIELD_GAP
    wall_height = wall_top - HORN_BOT_Y
    for x_wall in [x_ref - SHIELD_WIDTH/2, x_ref, x_ref + SHIELD_WIDTH/2]:
        ax_geo.add_patch(mpatches.FancyBboxPatch(
            (x_wall - HORN_WALL_T/2, HORN_BOT_Y), HORN_WALL_T, wall_height,
            boxstyle="square,pad=0", **horn_kw))

    ax_geo.legend(loc='upper right', fontsize=8)
    ax_geo.set_title(f"Geometría — {scenario_name}", fontsize=10)
    ax_geo.set_xlabel("X [u.MEEP]");  ax_geo.set_ylabel("Y [u.MEEP]")
    ax_geo.grid(True, linestyle=':', alpha=0.3)

    # Panel derecho: propiedades físicas de cada capa
    ax_info.axis('off')
    info_text = f"PROPIEDADES DEL SUELO\n{'─'*32}\n"
    for L in layers:
        info_text += layer_info_str(L) + "\n\n"
    ax_info.text(0.02, 0.98, info_text, transform=ax_info.transAxes,
                 fontsize=7.5, va='top', fontfamily='monospace',
                 bbox=dict(facecolor='#f5f5f0', alpha=0.9, boxstyle='round'))

    plt.suptitle(f"GPR FMCW — Caracterización de Suelo Agrícola\n{scenario_name}",
                 fontsize=11, y=1.01)
    plt.tight_layout()
    plt.show()


def plot_soil_bscan(x_positions, B, freqs, layers, B_raw_if, scenario_name):
    """
    Panel completo del B-scan para caracterización de suelo (3 subplots).

    [0,0] B-scan en dominio de profundidad aparente
    [0,1] Perfil medio de amplitud + detección de interfaces
    [1,0] Beat signals IF(t,x) — señal de audio bruta
    """
    BW_meep  = CHIRP_F2 - CHIRP_F1
    eps_ref  = layers[0]['eps']

    mask    = useful_freq_mask(freqs, layers)
    freqs_u = freqs[mask]
    B_u     = B[mask, :]
    depth_u = np.array([beat_freq_to_depth_meep(f, eps_ref) for f in freqs_u])
    depth_m = depth_u * A_MEEP

    vb      = np.percentile(np.abs(B_u), 98) * 0.5 or 1e-10
    vif     = np.percentile(np.abs(B_raw_if), 95) * 0.6 or 1e-10

    N_raw     = B_raw_if.shape[0]
    t_raw_max = N_raw * DT_RECORD

    ext_depth = [x_positions[0], x_positions[-1], depth_u[-1], depth_u[0]]
    ext_if    = [x_positions[0], x_positions[-1], t_raw_max,   0]

    fig = plt.figure(figsize=(18, 10))
    gs  = fig.add_gridspec(2, 3, hspace=0.4, wspace=0.35)

    ax_bscan  = fig.add_subplot(gs[0, :2])
    ax_prof   = fig.add_subplot(gs[0, 2])
    ax_if     = fig.add_subplot(gs[1, :2])
    ax_theta  = fig.add_subplot(gs[1, 2])

    fig.suptitle(
        f"GPR FMCW — Caracterización Agronómica: {scenario_name}\n"
        f"Chirp 1–2 GHz  |  {NUM_TRACES} trazas  |  "
        f"Mixer homodino + FFT",
        fontsize=11)

    # ── B-scan ──────────────────────────────────────────────────────────
    ax_bscan.imshow(B_u, aspect='auto', cmap='gray',
                    extent=ext_depth, vmin=-vb, vmax=vb,
                    origin='upper', interpolation='bilinear')
    ax_bscan.set_title("B-scan — profundidad aparente")
    ax_bscan.set_ylabel("Profundidad [u.MEEP]")
    ax_bscan.set_xlabel("Posición X [u.MEEP]")

    # Superponer profundidades teóricas de interfaces
    for L in layers:
        d_interf = abs(L['y_bot'])
        ax_bscan.axhline(d_interf, color='#FF7043', linestyle='--',
                         lw=0.9, alpha=0.8,
                         label=f"Interfaz @ {d_interf:.2f}u ({d_interf*A_MEEP:.2f}m)")

    # Segundo eje Y con metros
    ax_m = ax_bscan.twinx()
    ax_m.set_ylim(ax_bscan.get_ylim()[0] * A_MEEP,
                  ax_bscan.get_ylim()[1] * A_MEEP)
    ax_m.set_ylabel("Profundidad [m]")
    ax_bscan.legend(fontsize=7, loc='lower right', framealpha=0.8)
    ax_bscan.grid(True, linestyle=':', alpha=0.2)

    # ── Perfil medio de amplitud ────────────────────────────────────────
    mean_spec = np.mean(np.abs(B_u), axis=1)
    alpha_meep, alpha_dBm, rms_prof, fit_prof = estimate_attenuation_profile(
        B_u, depth_u, x_positions)

    ax_prof.plot(mean_spec / (np.max(mean_spec) + 1e-12), depth_u,
                 color='#1565C0', lw=1.2, label='Amplitud media')
    ax_prof.plot(rms_prof / (np.max(rms_prof) + 1e-12), depth_u,
                 color='#2E7D32', lw=1.0, linestyle='--', label='RMS')
    ax_prof.plot(fit_prof / (np.max(fit_prof) + 1e-12), depth_u,
                 color='#E65100', lw=0.8, linestyle=':', label=f'Exp. fit (α={alpha_dBm:.1f} dB/m)')

    for L in layers:
        d_int = abs(L['y_bot'])
        ax_prof.axhline(d_int, color='#FF7043', linestyle='--', lw=0.8, alpha=0.7)

    ax_prof.invert_yaxis()
    ax_prof.set_title(f"Perfil de amplitud\nα≈{alpha_dBm:.1f} dB/m")
    ax_prof.set_xlabel("Amplitud norm.")
    ax_prof.set_ylabel("Profundidad [u.MEEP]")
    ax_prof.legend(fontsize=7)
    ax_prof.grid(True, linestyle=':', alpha=0.3)

    # ── Beat signals IF ─────────────────────────────────────────────────
    ax_if.imshow(B_raw_if, aspect='auto', cmap='RdBu',
                 extent=ext_if, vmin=-vif, vmax=vif,
                 origin='upper', interpolation='bilinear')
    ax_if.set_title("Beat signals IF(t,x) — señal de audio al PC")
    ax_if.set_ylabel("Tiempo [u.MEEP]")
    ax_if.set_xlabel("Posición X [u.MEEP]")
    ax_if.grid(True, linestyle=':', alpha=0.2)

    # ── Perfil de humedad estimada ──────────────────────────────────────
    # Tomar el espectro de la traza central y convertir a θ
    idx_c    = np.argmin(np.abs(x_positions))
    spec_c   = np.abs(B_u[:, idx_c])
    depth_c  = depth_u

    # Suavizar y normalizar
    spec_smooth = uniform_filter1d(spec_c, size=max(3, len(spec_c)//20))

    # Escala heurística: la amplitud local alta → más contraste → más húmedo
    # Normalizar entre los valores reales de θ de las capas
    theta_min = min(topp_eps_to_theta(L['eps']) for L in layers)
    theta_max = max(topp_eps_to_theta(L['eps']) for L in layers)
    spec_n    = spec_smooth / (np.max(spec_smooth) + 1e-12)
    theta_est = theta_min + spec_n * (theta_max - theta_min)

    ax_theta.fill_betweenx(depth_c * A_MEEP, theta_est * 100, 0,
                           color='#1565C0', alpha=0.4)
    ax_theta.plot(theta_est * 100, depth_c * A_MEEP,
                  color='#1565C0', lw=1.3, label='θ estimado (%)')

    # Valores reales de θ por capa
    for L in layers:
        d_center_m = (abs(L['y_top']) + abs(L['y_bot'])) / 2 * A_MEEP
        theta_real = topp_eps_to_theta(L['eps']) * 100
        ax_theta.plot([theta_real, theta_real],
                      [abs(L['y_top']) * A_MEEP, abs(L['y_bot']) * A_MEEP],
                      color='#E65100', lw=2.5, alpha=0.7, label=f"Real θ={theta_real:.0f}%")

    ax_theta.invert_yaxis()
    ax_theta.set_title("Perfil θ — humedad estimada\nvs valores reales")
    ax_theta.set_xlabel("Humedad volumétrica [%]")
    ax_theta.set_ylabel("Profundidad [m]")
    ax_theta.set_xlim(0, 60)
    ax_theta.legend(fontsize=6, loc='lower right')
    ax_theta.grid(True, linestyle=':', alpha=0.3)

    plt.show()


def plot_inversion_report(x_positions, B, freqs, layers, scenario_name):
    """
    Reporte de inversión: tabla de parámetros del suelo estimados vs reales,
    mapa de humedad lateral y perfil de atenuación.
    """
    BW_meep  = CHIRP_F2 - CHIRP_F1
    eps_ref  = layers[0]['eps']
    mask     = useful_freq_mask(freqs, layers)
    freqs_u  = freqs[mask]
    B_u      = B[mask, :]
    depth_u  = np.array([beat_freq_to_depth_meep(f, eps_ref) for f in freqs_u])

    # Espectro promedio
    mean_spec = np.mean(np.abs(B_u), axis=1)

    # Inversión
    inv_results = invert_eps_from_spectrum(mean_spec, freqs_u, layers)

    # Atenuación
    alpha_meep, alpha_dBm, rms_prof, fit_prof = estimate_attenuation_profile(
        B_u, depth_u, x_positions)
    sigma_est = estimate_sigma_from_alpha(alpha_dBm, eps_ref)

    # ── Mapa lateral de θ superficial ──────────────────────────────────
    # Para cada traza, integrar la amplitud en el primer tercio del espectro
    # (correspondiente a la capa superficial) → proxy de humedad superficial
    idx_surf = max(1, len(depth_u) // 4)
    amp_surf = np.mean(np.abs(B_u[:idx_surf, :]), axis=0)
    theta_min = min(topp_eps_to_theta(L['eps']) for L in layers)
    theta_max = max(topp_eps_to_theta(L['eps']) for L in layers)
    amp_n     = amp_surf / (np.max(amp_surf) + 1e-12)
    theta_lateral = theta_min + amp_n * (theta_max - theta_min)

    # ── Figura ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 9))
    gs  = fig.add_gridspec(2, 2, hspace=0.45, wspace=0.35)
    ax_table  = fig.add_subplot(gs[0, 0])
    ax_theta  = fig.add_subplot(gs[0, 1])
    ax_atten  = fig.add_subplot(gs[1, 0])
    ax_map    = fig.add_subplot(gs[1, 1])

    fig.suptitle(
        f"Reporte de Inversión — {scenario_name}\n"
        f"Modelo de Topp (1980)  |  Atenuación exponencial  |  GPR FMCW 1–2 GHz",
        fontsize=11)

    if not inv_results:
        print("  [AVISO] No se detectaron interfaces en el espectro. "
              "Verificar parámetros de umbral o resolución.")
        return

    ax_table.axis('off')
    col_labels = ['Profund.\n(u.MEEP)', 'Profund.\n(m)', 'ε inv.',
                  'θ inv.\n(%)', 'ε real', 'θ real\n(%)', 'Error θ\n(%)']
    table_data = []

    for i, res in enumerate(inv_results):
        if i < len(layers):
            eps_real   = layers[i]['eps']
            theta_real = topp_eps_to_theta(eps_real) * 100
            err_theta  = abs(res['theta_inv']*100 - theta_real)
            eps_real_s = f"{eps_real:.1f}"
            theta_r_s  = f"{theta_real:.1f}"
            err_s      = f"{err_theta:.1f}"
        else:
            eps_real_s = "—";  theta_r_s = "—";  err_s = "—"
        table_data.append([
            f"{res['depth_meep']:.2f}",
            f"{res['depth_m']:.3f}",
            f"{res['eps_inv']:.1f}",
            f"{res['theta_inv']*100:.1f}",
            eps_real_s, theta_r_s, err_s,
        ])

    # Agregar fila de atenuación global
    table_data.append(['—', '—', '—', '—',
                       f"σ est: {sigma_est:.4f} S/m",
                       f"α: {alpha_dBm:.1f} dB/m", '—'])

    if table_data:
        tbl = ax_table.table(cellText=table_data, colLabels=col_labels,
                              loc='center', cellLoc='center')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1.2, 1.8)
        for (r, c), cell in tbl.get_celld().items():
            if r == 0:
                cell.set_facecolor('#1565C0')
                cell.set_text_props(color='white', fontweight='bold')
            elif r % 2 == 0:
                cell.set_facecolor('#E3F2FD')
    ax_table.set_title("Parámetros estimados vs reales", fontsize=9)

    # ── Perfil θ estimado vs real ───────────────────────────────────────
    depth_plot  = depth_u * A_MEEP
    theta_real_profile = np.zeros_like(depth_plot)
    for L in layers:
        mask_layer = (depth_plot >= abs(L['y_top']) * A_MEEP) & \
                     (depth_plot <= abs(L['y_bot']) * A_MEEP)
        theta_real_profile[mask_layer] = topp_eps_to_theta(L['eps']) * 100

    theta_from_spec = np.zeros_like(depth_plot)
    for res in inv_results:
        # Marcar una ventana de ±15 cm alrededor de cada interfaz detectada
        d_lo = max(0, res['depth_m'] - 0.15)
        d_hi = res['depth_m'] + 0.15
        mask_inv = (depth_plot >= d_lo) & (depth_plot <= d_hi)
        if mask_inv.any():
            theta_from_spec[mask_inv] = float(np.clip(res['theta_inv'] * 100, 0, 60))

    ax_theta.step(theta_real_profile, depth_plot, color='#E65100',
                  lw=2.0, label='θ real (simulado)', where='mid')
    ax_theta.step(theta_from_spec + 0.5, depth_plot, color='#1565C0',
                  lw=1.5, linestyle='--', label='θ estimado (GPR)', where='mid')
    ax_theta.fill_betweenx(depth_plot, theta_real_profile, 0,
                           color='#E65100', alpha=0.15, step='mid')
    ax_theta.invert_yaxis()
    ax_theta.set_xlabel("Humedad volumétrica [%]")
    ax_theta.set_ylabel("Profundidad [m]")
    ax_theta.set_xlim(0, 60)
    ax_theta.set_title("Perfil de humedad θ")
    ax_theta.legend(fontsize=8)
    ax_theta.grid(True, linestyle=':', alpha=0.3)

    # Rangos agronómicos de referencia
    ax_theta.axvline(15, color='gray', linestyle=':', lw=0.7, alpha=0.6)
    ax_theta.axvline(35, color='gray', linestyle=':', lw=0.7, alpha=0.6)
    ax_theta.text(7,  depth_plot[-1]*0.95, "Marchitez", fontsize=6, color='gray')
    ax_theta.text(20, depth_plot[-1]*0.95, "Cap. campo", fontsize=6, color='gray')
    ax_theta.text(40, depth_plot[-1]*0.95, "Saturado",  fontsize=6, color='gray')

    # ── Perfil de atenuación ────────────────────────────────────────────
    ax_atten.plot(rms_prof / (np.max(rms_prof) + 1e-12), depth_u * A_MEEP,
                  color='#1565C0', lw=1.3, label='RMS medido')
    ax_atten.plot(fit_prof / (np.max(fit_prof) + 1e-12), depth_u * A_MEEP,
                  color='#E65100', lw=1.0, linestyle='--',
                  label=f'Ajuste exp. (α={alpha_dBm:.1f} dB/m)')
    ax_atten.invert_yaxis()
    ax_atten.set_xlabel("Amplitud normalizada")
    ax_atten.set_ylabel("Profundidad [m]")
    ax_atten.set_title(f"Atenuación — σ estimado: {sigma_est:.4f} S/m")
    ax_atten.legend(fontsize=8)
    ax_atten.grid(True, linestyle=':', alpha=0.3)

    for L in layers:
        ax_atten.axhline(abs(L['y_bot']) * A_MEEP, color='#FF7043',
                         linestyle='--', lw=0.7, alpha=0.7)

    # ── Mapa lateral de humedad superficial ─────────────────────────────
    ax_map.fill_between(x_positions * A_MEEP, theta_lateral * 100,
                        alpha=0.4, color='#1565C0')
    ax_map.plot(x_positions * A_MEEP, theta_lateral * 100,
                color='#1565C0', lw=1.3)
    ax_map.axhline(topp_eps_to_theta(layers[0]['eps'])*100,
                   color='#E65100', linestyle='--', lw=1.0,
                   label=f"θ real capa sup. = {topp_eps_to_theta(layers[0]['eps'])*100:.0f}%")
    ax_map.set_xlabel("Posición X [m]")
    ax_map.set_ylabel("Humedad superficial estimada [%]")
    ax_map.set_ylim(0, 60)
    ax_map.set_title("Variación lateral de θ superficial")
    ax_map.legend(fontsize=8)
    ax_map.grid(True, linestyle=':', alpha=0.3)
    ax_map.axhline(15, color='gray', linestyle=':', lw=0.7, alpha=0.5)
    ax_map.axhline(35, color='gray', linestyle=':', lw=0.7, alpha=0.5)

    plt.show()

    # Imprimir resumen en consola
    print(f"\n{'='*60}")
    print(f"  REPORTE DE INVERSIÓN — {scenario_name}")
    print(f"{'='*60}")
    print(f"  α medido      : {alpha_dBm:.2f} dB/m")
    print(f"  σ estimado    : {sigma_est:.4f} S/m")
    for i, res in enumerate(inv_results):
        eps_real  = layers[i]['eps'] if i < len(layers) else '?'
        theta_r   = topp_eps_to_theta(eps_real)*100 if isinstance(eps_real, float) else 0
        print(f"  Interfaz {i+1}    : d={res['depth_m']:.2f}m  "
              f"ε_inv={res['eps_inv']:.1f} (real={eps_real})  "
              f"θ_inv={res['theta_inv']*100:.0f}% (real≈{theta_r:.0f}%)")
    print(f"{'='*60}\n")


# =============================================================================
# BLOQUE 9 — EJECUCIÓN PRINCIPAL
# =============================================================================

if __name__ == "__main__":

    # ------------------------------------------------------------------
    # SELECCIÓN DE ESCENARIO AGRÍCOLA
    # Cambiar esta línea para probar distintos escenarios:
    #
    #   scenario_suelo_seco()          — suelo en estrés hídrico, θ~5%
    #   scenario_suelo_humedo()        — post-lluvia, θ~40%, alta atenuación
    #   scenario_horizonte_abc()       — perfil pedológico A-B-C
    #   scenario_napa_freatica()       — napa a ~1m, reflector fuerte
    #   scenario_gradiente_humedad()   — variación lateral de θ
    #   scenario_suelo_compactado()    — piso de arado a 30 cm
    #   scenario_variabilidad_campo()  — perfil heterogéneo realista
    # ------------------------------------------------------------------
    layers, SCENARIO_NAME = scenario_napa_freatica()

    print(f"\n{'='*65}")
    print(f"  ESCENARIO  : {SCENARIO_NAME}")
    print(f"  Chirp      : {CHIRP_F1:.2f}–{CHIRP_F2:.2f} u.MEEP  ↔  1–2 GHz")
    print(f"  Trazas     : {NUM_TRACES}  |  t_max: {T_MAX} u.MEEP")
    print(f"  Objetivo   : caracterización agronómica de suelo")
    print(f"\n  Capas del escenario:")
    for L in layers:
        theta = topp_eps_to_theta(L['eps'])
        alpha = attenuation_dBm(L['eps'], L['sigma'])
        print(f"    {L['name']}")
        print(f"      ε={L['eps']:.1f}  σ={L['sigma']:.4f} S/m  "
              f"θ={theta:.3f} ({theta*100:.0f}%)  α={alpha:.1f} dB/m")
    print(f"{'='*65}\n")

    # PASO 1: visualizar geometría con propiedades físicas
    plot_geometry_soil(layers, SCENARIO_NAME, x_ref=0.0)

    # PASO 2: barrido B-scan completo
    x_positions = np.linspace(X_START, X_END, NUM_TRACES)
    bscan_rx    = []
    is_gradient = 'gradiente' in SCENARIO_NAME.lower()

    print(f"Iniciando B-scan: {NUM_TRACES} trazas ...")
    print("-" * 55)
    t0 = time.time()

    for i, x in enumerate(x_positions):
        eta = ""
        if i > 0:
            elapsed = time.time() - t0
            eta     = f"  ETA {elapsed/i*(NUM_TRACES-i)/60:.1f} min"
        print(f"  Traza {i+1:>3}/{NUM_TRACES}  x={x:+.3f}{eta}")

        if is_gradient:
            # Para el escenario de gradiente, variar ε con la posición x
            x_frac = (x - X_START) / (X_END - X_START)
            rx = simulate_ascan_soil(x, layers, x_frac=x_frac)
        else:
            rx = simulate_ascan_soil(x, layers)
        bscan_rx.append(rx)

    bscan_rx = np.array(bscan_rx)
    tx_ref   = make_reference_chirp(bscan_rx.shape[1])
    print(f"\n  Completado en {(time.time()-t0)/60:.1f} min.")

    # PASO 3: procesamiento FMCW
    B, freqs, B_raw_if, _ = process_bscan_fmcw(bscan_rx, tx_ref, layers)

    # PASO 4: panel B-scan con mapa de humedad
    plot_soil_bscan(x_positions, B, freqs, layers, B_raw_if, SCENARIO_NAME)

    # PASO 5: reporte de inversión (ε, θ, σ, profundidades)
    plot_inversion_report(x_positions, B, freqs, layers, SCENARIO_NAME)