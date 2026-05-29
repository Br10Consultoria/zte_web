const API_BASE = '/api';

function app() {
  return {
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

    // Toast
    toast: { show: false, message: '', type: 'info' },

    // OLTs
    olts: [],
    oltModal: false,
    oltModalEdit: false,
    oltForm: { name: '', ip: '', port: 22, username: '', password: '', protocol: 'ssh', snmp_community: 'public', snmp_version: '2c' },
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
        const data = await res.json();
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
        const data = await res.json();
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
        const data = await res.json();
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
            const ports = await res.json();
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
        if (res.ok) this.olts = await res.json();
      } catch (e) {}
    },

    openOLTModal() {
      this.oltModalEdit = false;
      this.oltEditId = null;
      this.oltForm = { name: '', ip: '', port: 22, username: '', password: '', protocol: 'ssh', snmp_community: 'public', snmp_version: '2c' };
      this.oltModal = true;
    },

    editOLT(olt) {
      this.oltModalEdit = true;
      this.oltEditId = olt.id;
      this.oltForm = { name: olt.name, ip: olt.ip, port: olt.port, username: olt.username, password: '', protocol: olt.protocol, snmp_community: olt.snmp_community || 'public', snmp_version: olt.snmp_version || '2c' };
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
        const data = await res.json();
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
        const data = await res.json();
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
        const data = await res.json();
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
          this.selectedOLTPorts = await res.json();
        }
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    openONUsByPort(olt, port) {
      this.onuFilter.olt_id = String(olt.id);
      this.loadOLTPortsForFilter();
      this.onuFilter.port_id = `${port.id}|${port.slot}|${port.card || 1}|${port.port}`;
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
        if (res.ok) this.filteredPorts = await res.json();
      } catch (e) {}
    },

    // ============================================================
    // ONUs
    // ============================================================
    async loadONUStatus(forceRefresh = false) {
      if (!this.onuFilter.port_id) return;
      const parts = this.onuFilter.port_id.split('|');
      // Suporta formato antigo (portId|slot|port) e novo (portId|slot|card|port)
      let slot, port;
      if (parts.length === 4) {
        [, slot, , port] = parts;
      } else {
        [, slot, port] = parts;
      }
      const oltId = this.onuFilter.olt_id;

      this.onuLoading = true;
      this.onuStatusData = null;
      try {
        const url = forceRefresh
          ? `/onus/${oltId}/pon/${slot}/${port}/status?force_refresh=true`
          : `/onus/${oltId}/pon/${slot}/${port}/status`;
        const res = await this.apiGet(url.replace('/api', ''));
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Erro ao consultar ONUs');
        }
        this.onuStatusData = await res.json();
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
          (o.last_down_cause && o.last_down_cause.toLowerCase().includes(q))
        );
      }
      if (this.onuStateFilter) {
        list = list.filter(o => o.oper_state === this.onuStateFilter);
      }
      return list;
    },

    async openONUDetail(onu) {
      if (!this.onuFilter.port_id) return;
      const parts = this.onuFilter.port_id.split('|');
      let slot, port;
      if (parts.length === 4) {
        [, slot, , port] = parts;
      } else {
        [, slot, port] = parts;
      }
      const onuId = onu.onu_index.split(':')[1];
      this.onuDetailContext = { oltId: this.onuFilter.olt_id, slot, port, onuId };
      this.onuDetailModal = true;
      this.detailTab = 'status';
      await this.fetchONUDetail(false);
    },

    async openONUDetailFromSearch(r) {
      const onuId = r.onu_index.split(':')[1];
      this.onuDetailContext = { oltId: this.searchOltId, slot: r.slot, card: r.card || 1, port: r.port, onuId };
      this.onuDetailModal = true;
      this.detailTab = 'status';
      await this.fetchONUDetail(false);
    },

    async fetchONUDetail(forceRefresh) {
      if (!this.onuDetailContext) return;
      const { oltId, slot, port, onuId } = this.onuDetailContext;
      this.onuDetailLoading = true;
      this.onuDetailData = null;
      try {
        const path = forceRefresh
          ? `/onus/${oltId}/pon/${slot}/${port}/onu/${onuId}/full?force_refresh=true`
          : `/onus/${oltId}/pon/${slot}/${port}/onu/${onuId}/full`;
        const res = await fetch(`${API_BASE}${path}`, {
          headers: { 'Authorization': `Bearer ${this.getToken()}` }
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Erro ao consultar ONU');
        }
        this.onuDetailData = await res.json();
      } catch (e) {
        this.showToast(e.message, 'error');
      } finally {
        this.onuDetailLoading = false;
      }
    },

    async refreshONUDetail() {
      await this.fetchONUDetail(true);
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
          const err = await res.json();
          throw new Error(err.detail || 'Erro');
        }
        this.uncfgData = await res.json();
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
        const data = await res.json();
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
        if (res.ok) this.users = await res.json();
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
        const data = await res.json();
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
        const data = await res.json();
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
        const data = await res.json();
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
        this.twoFAData = await res.json();
        this.twoFAConfirmCode = '';
        this.twoFASetupModal = true;
      } catch (e) {
        this.showToast(e.message, 'error');
      }
    },

    async confirmEnable2FA() {
      try {
        const res = await this.apiPost('/auth/2fa/enable', { totp_code: this.twoFAConfirmCode });
        const data = await res.json();
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
        const data = await res.json();
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
