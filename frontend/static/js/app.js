const API_BASE = '/api';

function app() {
  return {
    // Branding
    logoExists: false,
    logoSrc: '/static/img/logo.png',
    appTitle: 'ZTE TITAN',

    // Auth
    isLoggedIn: false,
    loginStep: 1,
    loginForm: { username: '', password: '' },
    twoFACode: '',
    loginLoading: false,
    loginError: '',
    showPass: false,
    partialToken: null,
    currentUser: null,

    // Navigation
    page: 'dashboard',
    sidebarCollapsed: false,

    // Toast
    toast: { show: false, message: '', type: 'info' },

    // OLTs
    olts: [],
    oltModal: false,
    oltModalEdit: false,
    oltForm: { name: '', ip: '', port: 22, username: '', password: '', protocol: 'ssh', snmp_community: 'public', snmp_version: '2c', olt_model: 'zte_c320' },
    oltEditId: null,
    selectedOLTPorts: null,
    selectedOLTForPorts: null,

    // ONUs
    onuFilter: { olt_id: '', port_id: '' },
    filteredPorts: [],
    onuStatusData: null,
    onuLoading: false,
    onuSearch: '',
    onuStateFilter: '',

    // ONU Detail
    onuDetailModal: false,
    onuDetailData: null,
    onuDetailLoading: false,
    onuDetailContext: null,
    detailTab: 'status',

    // ONU Traffic
    onuTrafficData: null,
    onuTrafficLoading: false,
    trafficAutoRefresh: false,
    trafficAutoRefreshTimer: null,
    trafficHistory: [],  // amostras para o gráfico

    // Unconfigured
    uncfgOltId: '',
    uncfgData: null,
    uncfgLoading: false,

    // Search
    searchOltId: '',
    searchSerial: '',
    searchResults: null,
    searchLoading: false,

    // Users
    users: [],
    userModal: false,
    userModalEdit: false,
    userForm: { username: '', password: '', full_name: '', email: '', role: 'viewer', is_active: true },
    userEditId: null,

    // Profile
    changePass: { current: '', new: '' },
    twoFASetupModal: false,
    twoFAData: null,
    twoFAConfirmCode: '',
    disable2FAModal: false,
    disable2FACode: '',

    // Reset Password
    resetPassModal: false,
    resetPassUser: null,
    resetPassNew: '',

    // Stats
    stats: {},

    // ============================================================
    // INIT
    // ============================================================
    async init() {
      // Verifica se logo existe
      try {
        const logoRes = await fetch('/static/img/logo.png', { method: 'HEAD' });
        this.logoExists = logoRes.ok;
      } catch (e) {
        this.logoExists = false;
      }

      const token = localStorage.getItem('zte_token');
      const user = localStorage.getItem('zte_user');
      if (token && user) {
        this.isLoggedIn = true;
        this.currentUser = JSON.parse(user);
        await this.loadDashboard();
        if (this.currentUser.role === 'admin') {
          await this.loadUsers();
        }
      }
    },

    // ============================================================
    // AUTH
    // ============================================================
    async doLogin() {
      this.loginLoading = true;
      this.loginError = '';
      try {
        const res = await fetch(`${API_BASE}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.loginForm)
        });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro ao fazer login');

        if (data.requires_2fa) {
          this.partialToken = data.access_token;
          this.loginStep = 2;
        } else {
          this.completeLogin(data);
        }
      } catch (e) {
        this.loginError = e.message;
      } finally {
        this.loginLoading = false;
      }
    },

    async verify2FA() {
      this.loginLoading = true;
      this.loginError = '';
      try {
        const res = await fetch(`${API_BASE}/auth/verify-2fa`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this.partialToken}`
          },
          body: JSON.stringify({ totp_code: this.twoFACode })
        });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Código inválido');
        this.completeLogin(data);
      } catch (e) {
        this.loginError = e.message;
      } finally {
        this.loginLoading = false;
      }
    },

    completeLogin(data) {
      localStorage.setItem('zte_token', data.access_token);
      localStorage.setItem('zte_user', JSON.stringify(data.user));
      this.currentUser = data.user;
      this.isLoggedIn = true;
      this.loginStep = 1;
      this.loginForm = { username: '', password: '' };
      this.twoFACode = '';
      this.loadDashboard();
      if (this.currentUser.role === 'admin') this.loadUsers();
    },

    logout() {
      localStorage.removeItem('zte_token');
      localStorage.removeItem('zte_user');
      this.isLoggedIn = false;
      this.currentUser = null;
      this.loginStep = 1;
      this.olts = [];
      this.onuStatusData = null;
    },

    getToken() {
      return localStorage.getItem('zte_token');
    },

    async apiGet(path, forceRefresh = false) {
      const url = forceRefresh ? `${API_BASE}${path}?force_refresh=true` : `${API_BASE}${path}`;
      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${this.getToken()}` }
      });
      if (res.status === 401 || res.status === 403) {
        this.logout();
        throw new Error('Sessão expirada');
      }
      return res;
    },

    async safeJson(res) {
      const text = await res.text().catch(() => '');
      try {
        return JSON.parse(text);
      } catch (e) {
        throw new Error(`Resposta inválida do servidor (HTTP ${res.status}): ${text.substring(0, 200)}`);
      }
    },

    async apiPost(path, body) {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.getToken()}`
        },
        body: JSON.stringify(body)
      });
      if (res.status === 401) { this.logout(); throw new Error('Sessão expirada'); }
      return res;
    },

    async apiPut(path, body) {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.getToken()}`
        },
        body: JSON.stringify(body)
      });
      if (res.status === 401) { this.logout(); throw new Error('Sessão expirada'); }
      return res;
    },

    async apiDelete(path) {
      const res = await fetch(`${API_BASE}${path}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${this.getToken()}` }
      });
      if (res.status === 401) { this.logout(); throw new Error('Sessão expirada'); }
      return res;
    },

    // ============================================================
    // DASHBOARD
    // ============================================================
    async loadDashboard() {
      await this.loadOLTs();
      // Health check
      try {
        const res = await fetch(`${API_BASE}/health`);
        const data = await this.safeJson(res);
        this.stats.redis = data.redis;
      } catch (e) {}
      this.stats.total_olts = this.olts.length;
      this.stats.online_olts = this.olts.filter(o => o.status === 'online').length;
      // Count ports
      let totalPorts = 0;
      for (const olt of this.olts) {
        try {
          const res = await this.apiGet(`/olts/${olt.id}/ports`);
          if (res.ok) {
            const ports = await this.safeJson(res);
            totalPorts += ports.length;
          }
        } catch (e) {}
      }
      this.stats.total_ports = totalPorts;
    },

    setPage(p) {
      this.page = p;
      if (p === 'users') this.loadUsers();
    },

    // ============================================================
    // OLTs
    // ============================================================
    async loadOLTs() {
      try {
        const res = await this.apiGet('/olts');
        if (res.ok) this.olts = await this.safeJson(res);
      } catch (e) {}
    },

    openOLTModal() {
      this.oltModalEdit = false;
      this.oltEditId = null;
      this.oltForm = { name: '', ip: '', port: 22, username: '', password: '', protocol: 'ssh', snmp_community: 'public', snmp_version: '2c', olt_model: 'zte_c320' };
      this.oltModal = true;
    },

    editOLT(olt) {
      this.oltModalEdit = true;
      this.oltEditId = olt.id;
      this.oltForm = { name: olt.name, ip: olt.ip, port: olt.port, username: olt.username, password: '', protocol: olt.protocol, snmp_community: olt.snmp_community || 'public', snmp_version: olt.snmp_version || '2c', olt_model: olt.olt_model || 'zte_c320' };
      this.oltModal = true;
    },

    async saveOLT() {
      try {
        let res;
        if (this.oltModalEdit) {
          res = await this.apiPut(`/olts/${this.oltEditId}`, this.oltForm);
        } else {
          res = await this.apiPost('/olts', this.oltForm);
        }
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro ao salvar OLT');
        this.oltModal = false;
        await this.loadOLTs();
        this.showToast(this.oltModalEdit ? 'OLT atualizada!' : 'OLT cadastrada!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async deleteOLT(olt) {
      if (!confirm(`Excluir OLT "${olt.name}"?`)) return;
      try {
        const res = await this.apiDelete(`/olts/${olt.id}`);
        if (!res.ok) throw new Error('Erro ao excluir');
        await this.loadOLTs();
        this.showToast('OLT excluída!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async testOLTConnection(olt) {
      this.showToast(`Testando conexão com ${olt.name}...`, 'info');
      try {
        const res = await this.apiPost(`/olts/${olt.id}/test-connection`, {});
        const data = await this.safeJson(res);
        if (data.success) {
          this.showToast(`✅ ${olt.name}: Conexão OK!`, 'success');
        } else {
          this.showToast(`❌ ${olt.name}: ${data.message}`, 'error');
        }
        await this.loadOLTs();
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async discoverOLT(olt) {
      this.showToast(`Iniciando descoberta de portas em ${olt.name}...`, 'info');
      try {
        const res = await this.apiPost(`/olts/${olt.id}/discover`, {});
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro na descoberta');
        this.showToast(`✅ ${data.message}`, 'success');
        await this.loadOLTs();
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async loadOLTPorts(olt) {
      this.selectedOLTForPorts = olt;
      try {
        const res = await this.apiGet(`/olts/${olt.id}/ports`);
        if (res.ok) {
          this.selectedOLTPorts = await this.safeJson(res);
        }
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    openONUsByPort(olt, port) {
      this.onuFilter.olt_id = String(olt.id);
      this.loadOLTPortsForFilter();
      // Formato: portId|slot|card|pon  (sintaxe ZTE: gpon-olt_SLOT/CARD/PON)
      this.onuFilter.port_id = `${port.id}|${port.slot}|${port.card || 1}|${port.pon}`;
      this.setPage('onus');
      this.loadONUStatus(false);
    },

    openONUPage(olt) {
      this.onuFilter.olt_id = String(olt.id);
      this.loadOLTPortsForFilter();
      this.setPage('onus');
    },

    async loadOLTPortsForFilter() {
      if (!this.onuFilter.olt_id) { this.filteredPorts = []; return; }
      try {
        const res = await this.apiGet(`/olts/${this.onuFilter.olt_id}/ports`);
        if (res.ok) this.filteredPorts = await this.safeJson(res);
      } catch (e) {}
    },

    // ============================================================
    // ONUs
    // ============================================================
    async loadONUStatus(forceRefresh = false) {
      if (!this.onuFilter.port_id) return;
      // Formato: portId|slot|card|pon
      const parts = this.onuFilter.port_id.split('|');
      const slot = parts[1];
      const card = parts[2] || '1';
      const pon  = parts[3] || parts[2]; // fallback para formato antigo portId|slot|pon
      const oltId = this.onuFilter.olt_id;

      this.onuLoading = true;
      this.onuStatusData = null;
      try {
        const url = forceRefresh
          ? `/onus/${oltId}/pon/${slot}/${card}/${pon}/status?force_refresh=true`
          : `/onus/${oltId}/pon/${slot}/${card}/${pon}/status`;
        const res = await this.apiGet(url);
        if (!res.ok) {
          const err = await this.safeJson(res);
          throw new Error(err.detail || 'Erro ao consultar ONUs');
        }
        this.onuStatusData = await this.safeJson(res);
      } catch (e) {
        this.showToast(e.message, 'error');
      } finally {
        this.onuLoading = false;
      }
    },

    get filteredONUs() {
      if (!this.onuStatusData) return [];
      let list = this.onuStatusData.onus || [];
      if (this.onuSearch) {
        const q = this.onuSearch.toLowerCase();
        list = list.filter(o =>
          o.onu_index.toLowerCase().includes(q) ||
          (o.serial && o.serial.toLowerCase().includes(q)) ||
          (o.model && o.model.toLowerCase().includes(q)) ||
          (o.onu_type && o.onu_type.toLowerCase().includes(q)) ||
          (o.description && o.description.toLowerCase().includes(q)) ||
          (o.last_down_cause && o.last_down_cause.toLowerCase().includes(q))
        );
      }
      if (this.onuStateFilter) {
        const f = this.onuStateFilter.toLowerCase();
        if (f === 'rx_ruim') {
          // Filtro especial: sinal óptico ruim (RX OLT abaixo de -28 dBm)
          list = list.filter(o => o.olt_rx_power !== null && o.olt_rx_power !== undefined && o.olt_rx_power < -28);
        } else {
          list = list.filter(o => (o.oper_state || '').toLowerCase() === f ||
            (o.phase_state || '').toLowerCase() === f ||
            (o.last_down_cause || '').toLowerCase() === f);
        }
      }
      return list;
    },

    closeONUDetail() {
      this.onuDetailModal = false;
      // Para auto-refresh de tráfego e limpa histórico
      if (this.trafficAutoRefresh) this.toggleTrafficAutoRefresh();
      this.onuTrafficData = null;
      this.trafficHistory = [];
    },

    async openONUDetail(onu) {
      if (!this.onuFilter.port_id) return;
      // Formato: portId|slot|card|pon
      const parts = this.onuFilter.port_id.split('|');
      const slot = parts[1];
      const card = parts[2] || '1';
      const pon  = parts[3] || parts[2];
      const onuId = onu.onu_index.split(':').pop();
      this.onuDetailContext = { oltId: this.onuFilter.olt_id, slot, card, pon, onuId };
      this.onuDetailModal = true;
      this.detailTab = 'info';
      await this.fetchONUDetail(false);
    },

    async openONUDetailFromSearch(r) {
      const onuId = r.onu_index.split(':').pop();
      this.onuDetailContext = { oltId: this.searchOltId, slot: r.slot, card: r.card || 1, pon: r.pon, onuId };
      this.onuDetailModal = true;
      this.detailTab = 'info';
      await this.fetchONUDetail(false);
    },

    async fetchONUDetail(forceRefresh) {
      if (!this.onuDetailContext) return;
      const { oltId, slot, card, pon, onuId } = this.onuDetailContext;
      this.onuDetailLoading = true;
      this.onuDetailData = null;
      try {
        const path = forceRefresh
          ? `/onus/${oltId}/pon/${slot}/${card}/${pon}/onu/${onuId}/full?force_refresh=true`
          : `/onus/${oltId}/pon/${slot}/${card}/${pon}/onu/${onuId}/full`;
        const res = await fetch(`${API_BASE}${path}`, {
          headers: { 'Authorization': `Bearer ${this.getToken()}` }
        });
        if (!res.ok) {
          const err = await this.safeJson(res);
          throw new Error(err.detail || 'Erro ao consultar ONU');
        }
        this.onuDetailData = await this.safeJson(res);
      } catch (e) {
        this.showToast(e.message, 'error');
      } finally {
        this.onuDetailLoading = false;
      }
    },

    async refreshONUDetail() {
      await this.fetchONUDetail(true);
    },

    async rebootONU() {
      if (!this.onuDetailContext) return;
      const { oltId, slot, card, pon, onuId } = this.onuDetailContext;
      const iface = this.onuDetailData ? (this.onuDetailData.onu_interface || `${slot}/${card}/${pon}:${onuId}`) : `${slot}/${card}/${pon}:${onuId}`;
      if (!confirm(`Confirmar reboot da ONU ${iface}?\n\nA ONU ficará offline por cerca de 60 segundos.`)) return;
      try {
        const res = await fetch(`${API_BASE}/onus/${oltId}/pon/${slot}/${card}/${pon}/onu/${onuId}/reboot`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${this.getToken()}` }
        });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro ao reiniciar ONU');
        this.showToast(`✅ ${data.message || 'Reboot enviado com sucesso!'}`, 'success');
      } catch (e) {
        this.showToast(`Erro: ${e.message}`, 'error');
      }
    },

    async loadONUTraffic() {
      if (!this.onuDetailContext) return;
      const { oltId, slot, card, pon, onuId } = this.onuDetailContext;
      this.onuTrafficLoading = true;
      try {
        const res = await fetch(`${API_BASE}/onus/${oltId}/pon/${slot}/${card}/${pon}/onu/${onuId}/traffic`, {
          headers: { 'Authorization': `Bearer ${this.getToken()}` }
        });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro ao consultar tráfego');
        this.onuTrafficData = data;
        // Adiciona amostra ao histórico para o gráfico
        if (data.traffic) {
          const now = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
          this.trafficHistory.push({
            time: now,
            rx: data.traffic.rx_bps || 0,
            tx: data.traffic.tx_bps || 0,
          });
          if (this.trafficHistory.length > 20) this.trafficHistory.shift();
          this.$nextTick(() => this.renderTrafficChart());
        }
      } catch (e) {
        this.showToast(`Erro ao carregar tráfego: ${e.message}`, 'error');
      } finally {
        this.onuTrafficLoading = false;
      }
    },

    toggleTrafficAutoRefresh() {
      this.trafficAutoRefresh = !this.trafficAutoRefresh;
      if (this.trafficAutoRefresh) {
        this.loadONUTraffic();
        this.trafficAutoRefreshTimer = setInterval(() => {
          if (this.detailTab === 'traffic' && this.onuDetailModal) {
            this.loadONUTraffic();
          } else {
            this.toggleTrafficAutoRefresh(); // para se sair da aba
          }
        }, 5000);
      } else {
        if (this.trafficAutoRefreshTimer) {
          clearInterval(this.trafficAutoRefreshTimer);
          this.trafficAutoRefreshTimer = null;
        }
      }
    },

    renderTrafficChart() {
      const canvas = document.getElementById('trafficChart');
      if (!canvas || this.trafficHistory.length < 2) return;
      const ctx = canvas.getContext('2d');
      const W = canvas.offsetWidth || 500;
      const H = 120;
      canvas.width = W;
      canvas.height = H;
      ctx.clearRect(0, 0, W, H);

      const rxVals = this.trafficHistory.map(s => s.rx);
      const txVals = this.trafficHistory.map(s => s.tx);
      const maxVal = Math.max(...rxVals, ...txVals, 1);
      const n = this.trafficHistory.length;
      const padL = 0, padR = 0, padT = 8, padB = 20;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;

      const xPos = (i) => padL + (i / (n - 1)) * plotW;
      const yPos = (v) => padT + plotH - (v / maxVal) * plotH;

      const drawLine = (vals, color, fillColor) => {
        ctx.beginPath();
        vals.forEach((v, i) => {
          const x = xPos(i), y = yPos(v);
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
        // Fill
        ctx.lineTo(xPos(n - 1), H - padB);
        ctx.lineTo(xPos(0), H - padB);
        ctx.closePath();
        ctx.fillStyle = fillColor;
        ctx.fill();
      };

      drawLine(rxVals, '#3fb950', 'rgba(63,185,80,0.15)');
      drawLine(txVals, '#58a6ff', 'rgba(88,166,255,0.15)');

      // Labels de tempo (início e fim)
      ctx.fillStyle = 'rgba(139,148,158,0.8)';
      ctx.font = '10px monospace';
      ctx.fillText(this.trafficHistory[0].time, padL + 2, H - 4);
      const lastLabel = this.trafficHistory[n - 1].time;
      ctx.fillText(lastLabel, W - ctx.measureText(lastLabel).width - 2, H - 4);

      // Legenda
      ctx.fillStyle = '#3fb950';
      ctx.fillRect(padL + 2, padT, 10, 3);
      ctx.fillStyle = 'rgba(139,148,158,0.8)';
      ctx.font = '9px sans-serif';
      ctx.fillText('RX', padL + 15, padT + 4);
      ctx.fillStyle = '#58a6ff';
      ctx.fillRect(padL + 40, padT, 10, 3);
      ctx.fillStyle = 'rgba(139,148,158,0.8)';
      ctx.fillText('TX', padL + 53, padT + 4);
    },

    formatBps(bps) {
      if (bps === null || bps === undefined) return '—';
      if (bps >= 1073741824) return (bps / 1073741824).toFixed(2) + ' Gbps';
      if (bps >= 1048576)    return (bps / 1048576).toFixed(2) + ' Mbps';
      if (bps >= 1024)       return (bps / 1024).toFixed(1) + ' Kbps';
      return bps + ' bps';
    },

    formatBytes(bytes) {
      if (bytes === null || bytes === undefined) return '—';
      if (bytes >= 1099511627776) return (bytes / 1099511627776).toFixed(2) + ' TB';
      if (bytes >= 1073741824)    return (bytes / 1073741824).toFixed(2) + ' GB';
      if (bytes >= 1048576)       return (bytes / 1048576).toFixed(1) + ' MB';
      if (bytes >= 1024)          return (bytes / 1024).toFixed(1) + ' KB';
      return bytes + ' B';
    },

    // ============================================================
    // UNCONFIGURED
    // ============================================================
    async loadUncfgONUs(forceRefresh = false) {
      if (!this.uncfgOltId) return;
      this.uncfgLoading = true;
      this.uncfgData = null;
      try {
        const path = forceRefresh
          ? `/onus/${this.uncfgOltId}/unconfigured?force_refresh=true`
          : `/onus/${this.uncfgOltId}/unconfigured`;
        const res = await fetch(`${API_BASE}${path}`, {
          headers: { 'Authorization': `Bearer ${this.getToken()}` }
        });
        if (!res.ok) {
          const err = await this.safeJson(res);
          throw new Error(err.detail || 'Erro');
        }
        this.uncfgData = await this.safeJson(res);
      } catch (e) {
        this.showToast(e.message, 'error');
      } finally {
        this.uncfgLoading = false;
      }
    },

    // ============================================================
    // SEARCH
    // ============================================================
    async searchONU() {
      if (!this.searchOltId || !this.searchSerial) return;
      this.searchLoading = true;
      this.searchResults = null;
      try {
        const res = await fetch(`${API_BASE}/onus/${this.searchOltId}/search?serial=${encodeURIComponent(this.searchSerial)}`, {
          headers: { 'Authorization': `Bearer ${this.getToken()}` }
        });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro na busca');
        this.searchResults = data.results;
      } catch (e) {
        this.showToast(e.message, 'error');
      } finally {
        this.searchLoading = false;
      }
    },

    // ============================================================
    // USERS
    // ============================================================
    async loadUsers() {
      try {
        const res = await this.apiGet('/auth/users');
        if (res.ok) this.users = await this.safeJson(res);
      } catch (e) {}
    },

    openUserModal() {
      this.userModalEdit = false;
      this.userEditId = null;
      this.userForm = { username: '', password: '', full_name: '', email: '', role: 'viewer', is_active: true };
      this.userModal = true;
    },

    editUser(u) {
      this.userModalEdit = true;
      this.userEditId = u.id;
      this.userForm = { username: u.username, full_name: u.full_name || '', email: u.email || '', role: u.role, is_active: u.is_active };
      this.userModal = true;
    },

    async saveUser() {
      try {
        let res;
        if (this.userModalEdit) {
          res = await this.apiPut(`/auth/users/${this.userEditId}`, {
            full_name: this.userForm.full_name,
            email: this.userForm.email,
            role: this.userForm.role,
            is_active: this.userForm.is_active
          });
        } else {
          res = await this.apiPost('/auth/users', this.userForm);
        }
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro ao salvar');
        this.userModal = false;
        await this.loadUsers();
        this.showToast(this.userModalEdit ? 'Usuário atualizado!' : 'Usuário criado!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async deleteUser(u) {
      if (!confirm(`Excluir usuário "${u.username}"?`)) return;
      try {
        const res = await this.apiDelete(`/auth/users/${u.id}`);
        if (!res.ok) throw new Error('Erro ao excluir');
        await this.loadUsers();
        this.showToast('Usuário excluído!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    resetUserPassword(u) {
      this.resetPassUser = u;
      this.resetPassNew = '';
      this.resetPassModal = true;
    },

    async confirmResetPassword() {
      try {
        const res = await this.apiPost(`/auth/users/${this.resetPassUser.id}/reset-password`, { new_password: this.resetPassNew });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro');
        this.resetPassModal = false;
        this.showToast('Senha redefinida!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    // ============================================================
    // PROFILE / 2FA
    // ============================================================
    async doChangePassword() {
      try {
        const res = await this.apiPost('/auth/change-password', {
          current_password: this.changePass.current,
          new_password: this.changePass.new
        });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Erro');
        this.changePass = { current: '', new: '' };
        this.showToast('Senha alterada com sucesso!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async setup2FA() {
      try {
        const res = await this.apiGet('/auth/2fa/setup');
        if (!res.ok) throw new Error('Erro ao configurar 2FA');
        this.twoFAData = await this.safeJson(res);
        this.twoFAConfirmCode = '';
        this.twoFASetupModal = true;
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async confirmEnable2FA() {
      try {
        const res = await this.apiPost('/auth/2fa/enable', { totp_code: this.twoFAConfirmCode });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Código inválido');
        this.twoFASetupModal = false;
        this.currentUser.is_2fa_enabled = true;
        localStorage.setItem('zte_user', JSON.stringify(this.currentUser));
        this.showToast('2FA ativado com sucesso!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async confirmDisable2FA() {
      try {
        const res = await this.apiPost('/auth/2fa/disable', { totp_code: this.disable2FACode });
        const data = await this.safeJson(res);
        if (!res.ok) throw new Error(data.detail || 'Código inválido');
        this.disable2FAModal = false;
        this.currentUser.is_2fa_enabled = false;
        localStorage.setItem('zte_user', JSON.stringify(this.currentUser));
        this.showToast('2FA desativado!', 'success');
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    // ============================================================
    // PRINT
    // ============================================================
    printONUTable() {
      const printContent = document.getElementById('onu-table');
      if (!printContent) return;
      const win = window.open('', '_blank');
      win.document.write(`
        <html><head><title>ONUs - ZTE Titan Manager</title>
        <style>
          body { font-family: Arial, sans-serif; font-size: 11px; color: #000; }
          table { width: 100%; border-collapse: collapse; }
          th { background: #e5e7eb; padding: 5px 8px; text-align: left; border: 1px solid #d1d5db; }
          td { padding: 4px 8px; border: 1px solid #e5e7eb; }
          .badge-green { background: #dcfce7; color: #166534; padding: 1px 6px; border-radius: 10px; }
          .badge-red { background: #fee2e2; color: #991b1b; padding: 1px 6px; border-radius: 10px; }
          .badge-yellow { background: #fef9c3; color: #854d0e; padding: 1px 6px; border-radius: 10px; }
          h2 { font-size: 13px; margin-bottom: 8px; }
        </style></head><body>
        <h2>Status das ONUs - ZTE Titan Manager</h2>
        <p style="font-size:10px;color:#666">Gerado em: ${new Date().toLocaleString('pt-BR')}</p>
        ${printContent.innerHTML}
        </body></html>
      `);
      win.document.close();
      win.print();
    },

    // ============================================================
    // TOAST
    // ============================================================
    showToast(message, type = 'info') {
      this.toast = { show: true, message, type };
      setTimeout(() => { this.toast.show = false; }, 4000);
    }
  };
}
