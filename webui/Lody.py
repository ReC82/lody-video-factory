"""Point d'entrée Streamlit de Lody Video Factory (prototype).

Lancement : ``streamlit run webui/Lody.py``. Indépendant de ``webui/Main.py``,
qui reste l'interface historique MoneyPrinterTurbo.
"""

import streamlit as st

st.set_page_config(
    page_title="Lody Video Factory",
    page_icon=":material/movie:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# set_page_config doit rester le premier appel Streamlit : import après.
from lody import home

home.render()
