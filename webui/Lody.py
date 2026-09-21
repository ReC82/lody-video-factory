"""Point d'entrée Streamlit de Lody Video Factory.

Lancement : ``streamlit run webui/Lody.py``. Indépendant de ``webui/Main.py``
(interface historique MoneyPrinterTurbo, conservée pour comparaison).
"""

import streamlit as st

st.set_page_config(
    page_title="Lody Video Factory",
    page_icon=":material/movie:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# set_page_config doit rester le premier appel Streamlit : import après.
from lody import app

app.render()
