from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from functools import wraps
import json, os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'oee-sistema-secret-2024')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///oee.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
TV_TOKEN = os.environ.get('TV_TOKEN', 'toiti-oee-tv-2024')

# Jinja filter
def from_json_filter(s):
    try: return json.loads(s)
    except: return []

# ─── MODELS ──────────────────────────────────────────────────────────────────

class ConfigSistema(db.Model):
    id                   = db.Column(db.Integer, primary_key=True)
    nome_empresa         = db.Column(db.String(100), default='Minha Fábrica')
    meta_oee             = db.Column(db.Float, default=65.0)
    meta_disponibilidade = db.Column(db.Float, default=90.0)
    meta_performance     = db.Column(db.Float, default=95.0)
    meta_qualidade       = db.Column(db.Float, default=99.0)
    mod_hora_a_hora      = db.Column(db.Boolean, default=False)
    mod_produto_lote     = db.Column(db.Boolean, default=False)
    mod_meta_hora        = db.Column(db.Boolean, default=False)
    mod_tv_dashboard     = db.Column(db.Boolean, default=True)
    mod_pareto           = db.Column(db.Boolean, default=True)
    mod_exportar_csv     = db.Column(db.Boolean, default=True)
    mod_api              = db.Column(db.Boolean, default=False)
    turnos_config        = db.Column(db.Text, default='[["1\u00b0 Turno","06:00","14:00"],["2\u00b0 Turno","14:00","22:00"],["3\u00b0 Turno","22:00","06:00"]]')
    atualizado_em        = db.Column(db.DateTime, default=datetime.utcnow)

class Usuario(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    nome       = db.Column(db.String(100), nullable=False)
    email      = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(200), nullable=False)
    perfil     = db.Column(db.String(20), default='operador')
    ativo      = db.Column(db.Boolean, default=True)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)
    def set_senha(self, s): self.senha_hash = generate_password_hash(s)
    def check_senha(self, s): return check_password_hash(self.senha_hash, s)

class Maquina(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    nome              = db.Column(db.String(100), nullable=False)
    codigo            = db.Column(db.String(30), unique=True, nullable=False)
    setor             = db.Column(db.String(100))
    tempo_ciclo_ideal = db.Column(db.Float, default=60.0)
    ativa             = db.Column(db.Boolean, default=True)
    criado_em         = db.Column(db.DateTime, default=datetime.utcnow)

class Produto(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    codigo    = db.Column(db.String(50), unique=True, nullable=False)
    descricao = db.Column(db.String(200), nullable=False)
    ativo     = db.Column(db.Boolean, default=True)

class RegistroProducao(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    maquina_id       = db.Column(db.Integer, db.ForeignKey('maquina.id'), nullable=False)
    usuario_id       = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    produto_id       = db.Column(db.Integer, db.ForeignKey('produto.id'), nullable=True)
    data             = db.Column(db.Date, nullable=False, default=date.today)
    turno            = db.Column(db.String(10), nullable=False)
    hora_registro    = db.Column(db.String(5), nullable=True)
    hora_inicio      = db.Column(db.Time, nullable=False)
    hora_fim         = db.Column(db.Time, nullable=False)
    pecas_produzidas = db.Column(db.Integer, default=0)
    pecas_boas       = db.Column(db.Integer, default=0)
    pecas_refugo     = db.Column(db.Integer, default=0)
    pecas_rejeito    = db.Column(db.Integer, default=0)
    meta_pecas       = db.Column(db.Integer, default=0)
    lote_planejado   = db.Column(db.Integer, default=0)
    tempo_parada_min = db.Column(db.Float, default=0)
    tipo_apontamento = db.Column(db.String(10), default='turno')
    criado_em        = db.Column(db.DateTime, default=datetime.utcnow)
    maquina  = db.relationship('Maquina', backref='registros')
    usuario  = db.relationship('Usuario', backref='registros')
    produto  = db.relationship('Produto', backref='registros')

    @property
    def tempo_total_min(self):
        from datetime import datetime, timedelta
        dt_i = datetime.combine(date.today(), self.hora_inicio)
        dt_f = datetime.combine(date.today(), self.hora_fim)
        if dt_f < dt_i: dt_f += timedelta(days=1)
        return (dt_f - dt_i).total_seconds() / 60

    @property
    def disponibilidade(self):
        tt = self.tempo_total_min
        if tt == 0: return 0
        return max(0, (tt - self.tempo_parada_min) / tt * 100)

    @property
    def performance(self):
        tt = self.tempo_total_min
        top = tt - self.tempo_parada_min
        if top == 0: return 0
        m = Maquina.query.get(self.maquina_id)
        return min(100, self.pecas_produzidas * (m.tempo_ciclo_ideal / 60) / top * 100)

    @property
    def qualidade(self):
        if self.pecas_produzidas == 0: return 0
        return self.pecas_boas / self.pecas_produzidas * 100

    @property
    def oee(self):
        return (self.disponibilidade/100) * (self.performance/100) * (self.qualidade/100) * 100

    @property
    def atingimento(self):
        if self.meta_pecas == 0: return None
        return round(self.pecas_boas / self.meta_pecas * 100, 1)

class Parada(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    maquina_id = db.Column(db.Integer, db.ForeignKey('maquina.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    data       = db.Column(db.Date, nullable=False, default=date.today)
    turno      = db.Column(db.String(10), nullable=False)
    hora_inicio= db.Column(db.Time, nullable=False)
    hora_fim   = db.Column(db.Time)
    motivo     = db.Column(db.String(200), nullable=False)
    categoria  = db.Column(db.String(50))
    observacao = db.Column(db.Text)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)
    maquina    = db.relationship('Maquina', backref='paradas')
    usuario    = db.relationship('Usuario', backref='paradas')

    @property
    def duracao_min(self):
        if not self.hora_fim: return None
        from datetime import datetime, timedelta
        dt_i = datetime.combine(date.today(), self.hora_inicio)
        dt_f = datetime.combine(date.today(), self.hora_fim)
        if dt_f < dt_i: dt_f += timedelta(days=1)
        return (dt_f - dt_i).total_seconds() / 60

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_config():
    c = ConfigSistema.query.first()
    if not c:
        c = ConfigSistema()
        db.session.add(c)
        db.session.commit()
    return c

def calc_stats(registros):
    if not registros:
        return {'oee':0,'disponibilidade':0,'performance':0,'qualidade':0,
                'pecas_boas':0,'pecas_refugo':0,'pecas_rejeito':0,
                'total_paradas_min':0,'num_registros':0,'meta_pecas':0,'pecas_produzidas':0}
    return {
        'oee': sum(r.oee for r in registros)/len(registros),
        'disponibilidade': sum(r.disponibilidade for r in registros)/len(registros),
        'performance': sum(r.performance for r in registros)/len(registros),
        'qualidade': sum(r.qualidade for r in registros)/len(registros),
        'pecas_boas': sum(r.pecas_boas for r in registros),
        'pecas_produzidas': sum(r.pecas_produzidas for r in registros),
        'pecas_refugo': sum(r.pecas_refugo for r in registros),
        'pecas_rejeito': sum(r.pecas_rejeito for r in registros),
        'total_paradas_min': sum(r.tempo_parada_min for r in registros),
        'num_registros': len(registros),
        'meta_pecas': sum(r.meta_pecas for r in registros),
    }

def login_required(f):
    @wraps(f)
    def d(*a,**k):
        if 'usuario_id' not in session: return redirect(url_for('login'))
        return f(*a,**k)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a,**k):
        if 'usuario_id' not in session: return redirect(url_for('login'))
        if session.get('perfil') not in ['admin','gestor']:
            flash('Acesso negado.','error'); return redirect(url_for('dashboard'))
        return f(*a,**k)
    return d

# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route('/')
def index(): return redirect(url_for('dashboard') if 'usuario_id' in session else url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = Usuario.query.filter_by(email=request.form.get('email','').strip().lower(), ativo=True).first()
        if u and u.check_senha(request.form.get('senha','')):
            session.update({'usuario_id':u.id,'nome':u.nome,'perfil':u.perfil})
            return redirect(url_for('dashboard'))
        flash('E-mail ou senha incorretos.','error')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    hoje = date.today(); cfg = get_config()
    registros_hoje = RegistroProducao.query.filter_by(data=hoje).all()
    stats = calc_stats(registros_hoje)
    paradas_hoje = Parada.query.filter_by(data=hoje).order_by(Parada.hora_inicio.desc()).limit(10).all()

    oee_semana = []
    for i in range(6,-1,-1):
        d = hoje - timedelta(days=i)
        regs = RegistroProducao.query.filter_by(data=d).all()
        oee_semana.append({'data':d.strftime('%d/%m'),'oee':round(sum(r.oee for r in regs)/len(regs),1) if regs else 0})

    maquinas_status = []
    for m in Maquina.query.filter_by(ativa=True).all():
        reg = RegistroProducao.query.filter_by(maquina_id=m.id,data=hoje).order_by(RegistroProducao.criado_em.desc()).first()
        oee_val = round(reg.oee,1) if reg else None
        st = 'ok' if reg and reg.oee>=cfg.meta_oee else ('alerta' if reg and reg.oee>=50 else ('critico' if reg else 'sem_dados'))
        maquinas_status.append({'maquina':m,'oee':oee_val,'pecas_boas':reg.pecas_boas if reg else 0,'status':st})

    pareto = {}
    for p in paradas_hoje:
        cat = p.categoria or 'Outros'
        pareto[cat] = pareto.get(cat,0) + (p.duracao_min or 0)
    pareto_paradas = sorted([{'categoria':k,'minutos':round(v)} for k,v in pareto.items()],key=lambda x:x['minutos'],reverse=True)

    hh_dados = []
    if cfg.mod_hora_a_hora:
        for r in RegistroProducao.query.filter_by(data=hoje,tipo_apontamento='hora').order_by(RegistroProducao.hora_registro).all():
            hh_dados.append({'hora':r.hora_registro,'real':r.pecas_boas,'meta':r.meta_pecas,'oee':round(r.oee,1),'ating':r.atingimento})

    return render_template('dashboard.html', stats=stats, cfg=cfg,
        paradas_hoje=paradas_hoje, oee_semana=json.dumps(oee_semana),
        maquinas_status=maquinas_status, pareto_paradas=json.dumps(pareto_paradas),
        hh_dados=json.dumps(hh_dados), hoje=hoje)

@app.route('/apontar', methods=['GET','POST'])
@login_required
def apontar():
    cfg = get_config()
    maquinas = Maquina.query.filter_by(ativa=True).all()
    produtos  = Produto.query.filter_by(ativo=True).all() if cfg.mod_produto_lote else []
    if request.method == 'POST':
        tipo = request.form.get('tipo_apontamento','turno')
        pp = int(request.form.get('pecas_produzidas',0))
        pr = int(request.form.get('pecas_refugo',0))
        pj = int(request.form.get('pecas_rejeito',0))
        pb = max(0, pp - pr - pj)
        reg = RegistroProducao(
            maquina_id=request.form.get('maquina_id'),
            usuario_id=session['usuario_id'],
            produto_id=request.form.get('produto_id') or None,
            data=datetime.strptime(request.form.get('data'),'%Y-%m-%d').date(),
            turno=request.form.get('turno'),
            hora_registro=request.form.get('hora_registro','') if tipo=='hora' else None,
            hora_inicio=datetime.strptime(request.form.get('hora_inicio'),'%H:%M').time(),
            hora_fim=datetime.strptime(request.form.get('hora_fim'),'%H:%M').time(),
            pecas_produzidas=pp, pecas_boas=pb, pecas_refugo=pr, pecas_rejeito=pj,
            meta_pecas=int(request.form.get('meta_pecas',0)),
            lote_planejado=int(request.form.get('lote_planejado',0)),
            tempo_parada_min=float(request.form.get('tempo_parada_min',0)),
            tipo_apontamento=tipo
        )
        db.session.add(reg); db.session.commit()
        flash('Produção registrada!','success')
        return redirect(url_for('apontar'))
    hoje = date.today().strftime('%Y-%m-%d')
    horas_lista = [f'{h:02d}h' for h in range(6,23)]
    return render_template('apontar.html', cfg=cfg, maquinas=maquinas, produtos=produtos,
                           hoje=hoje, hora_atual=datetime.now().strftime('%H:%M'), horas_lista=horas_lista)

@app.route('/paradas', methods=['GET','POST'])
@login_required
def paradas():
    maquinas = Maquina.query.filter_by(ativa=True).all()
    if request.method == 'POST':
        hf_s = request.form.get('hora_fim','')
        p = Parada(
            maquina_id=request.form.get('maquina_id'), usuario_id=session['usuario_id'],
            data=datetime.strptime(request.form.get('data'),'%Y-%m-%d').date(),
            turno=request.form.get('turno'),
            hora_inicio=datetime.strptime(request.form.get('hora_inicio'),'%H:%M').time(),
            hora_fim=datetime.strptime(hf_s,'%H:%M').time() if hf_s else None,
            motivo=request.form.get('motivo'), categoria=request.form.get('categoria'),
            observacao=request.form.get('observacao','')
        )
        db.session.add(p); db.session.commit()
        flash('Parada registrada!','success')
        return redirect(url_for('paradas'))
    return render_template('paradas.html', maquinas=maquinas,
        paradas=Parada.query.order_by(Parada.data.desc(),Parada.hora_inicio.desc()).limit(50).all(),
        hoje=date.today().strftime('%Y-%m-%d'), hora_atual=datetime.now().strftime('%H:%M'))

@app.route('/relatorios')
@login_required
def relatorios():
    cfg = get_config()
    data_ini_str = request.args.get('data_ini',(date.today()-timedelta(days=30)).strftime('%Y-%m-%d'))
    data_fim_str = request.args.get('data_fim',date.today().strftime('%Y-%m-%d'))
    maquina_id = request.args.get('maquina_id','')
    turno_fil  = request.args.get('turno','')
    data_ini = datetime.strptime(data_ini_str,'%Y-%m-%d').date()
    data_fim = datetime.strptime(data_fim_str,'%Y-%m-%d').date()

    q = RegistroProducao.query.filter(RegistroProducao.data>=data_ini,RegistroProducao.data<=data_fim)
    if maquina_id: q = q.filter_by(maquina_id=maquina_id)
    if turno_fil: q = q.filter_by(turno=turno_fil)
    registros = q.order_by(RegistroProducao.data.desc()).all()

    stats_periodo = calc_stats(registros)

    oee_maq = {}
    for r in registros:
        mid = r.maquina_id
        if mid not in oee_maq: oee_maq[mid] = {'nome':r.maquina.nome,'oees':[],'pecas':0,'refugo':0,'rejeito':0}
        oee_maq[mid]['oees'].append(r.oee); oee_maq[mid]['pecas'] += r.pecas_boas
        oee_maq[mid]['refugo'] += r.pecas_refugo; oee_maq[mid]['rejeito'] += r.pecas_rejeito
    resumo_maquinas = sorted([{'nome':v['nome'],'oee':round(sum(v['oees'])/len(v['oees']),1),
        'pecas':v['pecas'],'refugo':v['refugo'],'rejeito':v['rejeito']} for v in oee_maq.values()],key=lambda x:x['oee'],reverse=True)

    oee_turno = {}
    for r in registros:
        if r.turno not in oee_turno: oee_turno[r.turno] = []
        oee_turno[r.turno].append(r.oee)
    resumo_turnos = [{'turno':t,'oee':round(sum(v)/len(v),1)} for t,v in oee_turno.items()]

    oee_dia = {}
    for r in registros:
        d = r.data.strftime('%d/%m')
        if d not in oee_dia: oee_dia[d] = []
        oee_dia[d].append(r.oee)
    oee_diario = sorted([{'data':d,'oee':round(sum(v)/len(v),1)} for d,v in oee_dia.items()],key=lambda x:x['data'])

    return render_template('relatorios.html', cfg=cfg, registros=registros,
        maquinas=Maquina.query.filter_by(ativa=True).all(),
        resumo_maquinas=resumo_maquinas, resumo_turnos=resumo_turnos,
        stats_periodo=stats_periodo, oee_diario=json.dumps(oee_diario),
        data_ini=data_ini_str, data_fim=data_fim_str,
        maquina_id_sel=maquina_id, turno_sel=turno_fil)

@app.route('/api/exportar-csv')
@login_required
def exportar_csv():
    cfg = get_config()
    data_ini = datetime.strptime(request.args.get('data_ini',(date.today()-timedelta(days=30)).strftime('%Y-%m-%d')),'%Y-%m-%d').date()
    data_fim = datetime.strptime(request.args.get('data_fim',date.today().strftime('%Y-%m-%d')),'%Y-%m-%d').date()
    registros = RegistroProducao.query.filter(RegistroProducao.data>=data_ini,RegistroProducao.data<=data_fim).all()
    cab = 'Data,Turno,Hora,Máquina,Setor'
    if cfg.mod_produto_lote: cab += ',Produto,Lote'
    cab += ',Produzidas,Boas,Refugo,Rejeito'
    if cfg.mod_meta_hora: cab += ',Meta'
    cab += ',Parada(min),Disp(%),Perf(%),Qual(%),OEE(%)'
    if cfg.mod_meta_hora: cab += ',Atingimento(%)'
    linhas = [cab]
    for r in registros:
        l = f"{r.data},{r.turno},{r.hora_registro or ''},{r.maquina.nome},{r.maquina.setor or ''}"
        if cfg.mod_produto_lote: l += f",{r.produto.codigo if r.produto else ''},{r.lote_planejado}"
        l += f",{r.pecas_produzidas},{r.pecas_boas},{r.pecas_refugo},{r.pecas_rejeito}"
        if cfg.mod_meta_hora: l += f",{r.meta_pecas}"
        l += f",{r.tempo_parada_min:.1f},{r.disponibilidade:.1f},{r.performance:.1f},{r.qualidade:.1f},{r.oee:.1f}"
        if cfg.mod_meta_hora: l += f",{r.atingimento or ''}"
        linhas.append(l)
    return Response('\n'.join(linhas),mimetype='text/csv',
        headers={'Content-Disposition':f'attachment; filename=oee_{data_ini}_{data_fim}.csv'})

@app.route('/maquinas', methods=['GET','POST'])
@admin_required
def maquinas():
    if request.method == 'POST':
        acao = request.form.get('acao')
        if acao == 'criar':
            m = Maquina(nome=request.form.get('nome'),codigo=request.form.get('codigo'),
                setor=request.form.get('setor'),tempo_ciclo_ideal=float(request.form.get('tempo_ciclo_ideal',60)))
            db.session.add(m); db.session.commit(); flash('Máquina cadastrada!','success')
        elif acao == 'toggle':
            m = Maquina.query.get(request.form.get('id'))
            if m: m.ativa = not m.ativa; db.session.commit()
    return render_template('maquinas.html',maquinas=Maquina.query.order_by(Maquina.ativa.desc(),Maquina.nome).all())

@app.route('/produtos', methods=['GET','POST'])
@admin_required
def produtos():
    cfg = get_config()
    if not cfg.mod_produto_lote:
        flash('Módulo Produto/Lote não está ativo.','error'); return redirect(url_for('configuracoes'))
    if request.method == 'POST':
        acao = request.form.get('acao')
        if acao == 'criar':
            p = Produto(codigo=request.form.get('codigo'),descricao=request.form.get('descricao'))
            db.session.add(p); db.session.commit(); flash('Produto cadastrado!','success')
        elif acao == 'toggle':
            p = Produto.query.get(request.form.get('id'))
            if p: p.ativo = not p.ativo; db.session.commit()
    return render_template('produtos.html',produtos=Produto.query.order_by(Produto.ativo.desc(),Produto.codigo).all())

@app.route('/usuarios', methods=['GET','POST'])
@admin_required
def usuarios():
    if request.method == 'POST':
        acao = request.form.get('acao')
        if acao == 'criar':
            u = Usuario(nome=request.form.get('nome'),email=request.form.get('email').lower(),perfil=request.form.get('perfil','operador'))
            u.set_senha(request.form.get('senha')); db.session.add(u); db.session.commit(); flash('Usuário criado!','success')
        elif acao == 'toggle':
            u = Usuario.query.get(request.form.get('id'))
            if u and u.id != session['usuario_id']: u.ativo = not u.ativo; db.session.commit()
    return render_template('usuarios.html',usuarios=Usuario.query.order_by(Usuario.ativo.desc(),Usuario.nome).all())

@app.route('/configuracoes', methods=['GET','POST'])
@admin_required
def configuracoes():
    cfg = get_config()
    if request.method == 'POST':
        cfg.nome_empresa         = request.form.get('nome_empresa',cfg.nome_empresa)
        cfg.meta_oee             = float(request.form.get('meta_oee',cfg.meta_oee))
        cfg.meta_disponibilidade = float(request.form.get('meta_disponibilidade',cfg.meta_disponibilidade))
        cfg.meta_performance     = float(request.form.get('meta_performance',cfg.meta_performance))
        cfg.meta_qualidade       = float(request.form.get('meta_qualidade',cfg.meta_qualidade))
        cfg.mod_hora_a_hora  = 'mod_hora_a_hora'  in request.form
        cfg.mod_produto_lote = 'mod_produto_lote' in request.form
        cfg.mod_meta_hora    = 'mod_meta_hora'    in request.form
        cfg.mod_tv_dashboard = 'mod_tv_dashboard' in request.form
        cfg.mod_pareto       = 'mod_pareto'       in request.form
        cfg.mod_exportar_csv = 'mod_exportar_csv' in request.form
        cfg.mod_api          = 'mod_api'          in request.form
        tc = request.form.get('turnos_config','')
        if tc: cfg.turnos_config = tc
        cfg.atualizado_em    = datetime.utcnow()
        db.session.commit(); flash('Configurações salvas!','success')
        return redirect(url_for('configuracoes'))
    return render_template('configuracoes.html',cfg=cfg)

@app.route('/tv')
def tv_dashboard():
    cfg = get_config()
    if not cfg.mod_tv_dashboard:
        return '<h1 style="font-family:sans-serif;color:#666;text-align:center;margin-top:40vh">Módulo TV não ativo</h1>',403
    if request.args.get('token','') != TV_TOKEN:
        return '<h1 style="font-family:sans-serif;color:red;text-align:center;margin-top:40vh">Token inválido</h1>',403
    hoje = date.today()
    registros_hoje = RegistroProducao.query.filter_by(data=hoje).all()
    stats = calc_stats(registros_hoje)
    maquinas_status = []
    for m in Maquina.query.filter_by(ativa=True).all():
        reg = RegistroProducao.query.filter_by(maquina_id=m.id,data=hoje).order_by(RegistroProducao.criado_em.desc()).first()
        oee_val = round(reg.oee,1) if reg else None
        st = 'ok' if reg and reg.oee>=cfg.meta_oee else ('alerta' if reg and reg.oee>=50 else ('critico' if reg else 'sem_dados'))
        operador = Usuario.query.get(reg.usuario_id).nome if reg else None
        ultimo_ap = reg.criado_em.strftime('%H:%M') if reg else None
        maquinas_status.append({'maquina':m,'oee':oee_val,'pecas_boas':reg.pecas_boas if reg else 0,'status':st,'operador':operador,'ultimo_apontamento':ultimo_ap})
    paradas_hoje = Parada.query.filter_by(data=hoje).order_by(Parada.hora_inicio.desc()).limit(8).all()
    return render_template('tv.html',cfg=cfg,stats=stats,maquinas_status=maquinas_status,
        paradas_hoje=paradas_hoje,
        hoje=hoje,token=request.args.get('token',''))

app.jinja_env.filters['from_json'] = from_json_filter

def init_db():
    with app.app_context():
        db.create_all()
        # Migration: add new columns if missing
        from sqlalchemy import text
        with db.engine.connect() as conn:
            for col, default in [
                ('turnos_config', '"[[\"1\u00b0 Turno\",\"06:00\",\"14:00\"],[\"2\u00b0 Turno\",\"14:00\",\"22:00\"],[\"3\u00b0 Turno\",\"22:00\",\"06:00\"]]"'),
                ('pecas_rejeito', '0'),
                ('meta_pecas', '0'),
                ('lote_planejado', '0'),
                ('tipo_apontamento', '"turno"'),
                ('hora_registro', 'NULL'),
                ('produto_id', 'NULL'),
            ]:
                try:
                    conn.execute(text(f'ALTER TABLE config_sistema ADD COLUMN {col} TEXT DEFAULT {default}'))
                    conn.commit()
                except: pass
                try:
                    conn.execute(text(f'ALTER TABLE registro_producao ADD COLUMN {col} TEXT DEFAULT {default}'))
                    conn.commit()
                except: pass
        if not Usuario.query.filter_by(email='admin@fabrica.com').first():
            admin = Usuario(nome='Administrador',email='admin@fabrica.com',perfil='admin')
            admin.set_senha('admin123'); db.session.add(admin)
        if not ConfigSistema.query.first():
            db.session.add(ConfigSistema())
        db.session.commit()
        print('✅ Banco criado. Login: admin@fabrica.com / admin123')

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
