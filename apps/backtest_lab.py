"""Four-page research workflow; navigation never runs simulations."""
import streamlit as st
from lobcap.backtest.research_flow.workbench import render_workbench
from lobcap.config.backtest import load_backtest_config
from lobcap.config.experiment import ROOT
st.set_page_config(page_title='마켓메이킹 연구', layout='wide')
render_workbench(load_backtest_config(ROOT/'configs/backtest/binance_recorded.yaml'))
