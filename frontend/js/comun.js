// Funciones comunes de los paneles (admin, chofer, cliente).

function getCookie(name) { const m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)')); return m ? m[2] : null; }

// Escapa datos de la DB antes de meterlos en innerHTML (XSS almacenado).
function esc(v) { return v == null ? '' : String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
// Argumento de texto para handlers inline: onclick="fn(${jsArg(x)})".
function jsArg(v) { return esc(JSON.stringify(v == null ? '' : String(v))); }

function dinero(v) { return v == null ? '-' : '$ ' + Number(v).toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function km(v) { return v == null ? '-' : Number(v).toLocaleString('es-AR', { maximumFractionDigits: 1 }) + ' km'; }
function numFlete(id) { return '#FLT-' + String(id).padStart(6, '0'); }

// Pedido a la API con CSRF. Devuelve el JSON; si falla tira Error con el mensaje del servidor.
async function api(url, metodo = 'GET', datos) {
    const opciones = { method: metodo, headers: {} };
    if (metodo !== 'GET') {
        opciones.headers['X-CSRF-Token'] = getCookie('csrf_token') || '';
        if (datos !== undefined) { opciones.headers['Content-Type'] = 'application/json'; opciones.body = JSON.stringify(datos); }
    }
    let res;
    try { res = await fetch(url, opciones); } catch (e) { throw new Error('Sin conexión con el servidor.'); }
    if (res.status === 401) { window.location.href = '/index.html'; throw new Error('Sesión vencida.'); }
    const cuerpo = await res.json().catch(() => null);
    if (!res.ok) {
        let msg = cuerpo && cuerpo.detail;
        if (Array.isArray(msg)) msg = 'Revise los datos: ' + msg.map(e => (e.loc || []).slice(-1)[0]).join(', ');
        throw new Error(msg || ('Error ' + res.status));
    }
    return cuerpo;
}

// Usuario de la sesión (rol real del servidor; la cookie user_rol es solo para la interfaz).
async function quienSoy() { return api('/api/me'); }

async function cerrarSesion() {
    try { await api('/api/logout', 'POST'); } catch (e) { /* igual se vuelve al login */ }
    window.location.href = '/index.html';
}

// Coordenadas desde lo que se pegue: "-34.60, -58.38", un link de Google Maps (.../@-34.60,-58.38,15z,
// ?q=-34.60,-58.38, ll=..., !3d-34.60!4d-58.38). Devuelve {lat, lon} o null.
function parseCoords(texto) {
    if (!texto) return null;
    const t = decodeURIComponent(String(texto)).trim();
    const patrones = [
        /!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/,
        /@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/,
        /[?&](?:q|ll|query|destination|center)=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/,
        /^(-?\d+(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d+(?:[.,]\d+)?)$/,
    ];
    for (const p of patrones) {
        const m = t.match(p);
        if (m) {
            const lat = parseFloat(m[1].replace(',', '.')), lon = parseFloat(m[2].replace(',', '.'));
            if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) return { lat, lon };
        }
    }
    return null;
}
function textoCoords(lat, lon) { return (lat == null || lon == null) ? '' : `${Number(lat).toFixed(6)}, ${Number(lon).toFixed(6)}`; }
function badgeUbicacion(lat, lon) {
    return (lat == null || lon == null)
        ? '<span class="badge badge-warning" title="Sin coordenadas: no entra en el cálculo de km">Sin ubicar</span>'
        : `<a class="badge badge-success" href="https://www.google.com/maps?q=${lat},${lon}" target="_blank" rel="noopener" title="Ver en Google Maps">Ubicada</a>`;
}
// Lee un campo de ubicación: vacío = sin ubicar ({lat:null, lon:null}); texto que no se entiende = error.
function leerUbicacion(input) {
    const v = (input.value || '').trim();
    if (!v) return { lat: null, lon: null };
    const c = parseCoords(v);
    if (!c) { input.classList.add('is-invalid'); throw new Error('No se entiende la ubicación: pegue el link de Google Maps o "latitud, longitud".'); }
    return c;
}

// Barra para que un admin pase de un panel a otro. En chofer/cliente solo aparece si el rol real es admin.
function barraPaneles(actual, rol) {
    if (rol !== 'admin') return '';
    const paneles = [['admin', 'Admin'], ['chofer', 'Ver como chofer'], ['cliente', 'Ver como cliente']];
    return paneles.filter(p => p[0] !== actual).map(p =>
        `<a class="btn btn-secondary btn-panel" href="/${p[0]}.html">${p[0] === 'admin' ? '&#8592; Volver al admin' : p[1]}</a>`).join('');
}

// Selector de un punto (origen o destino) de una cotizacion: una direccion guardada o una cargada a mano.
// fuentes: [{grupo, items: [{id, alias, calle, altura, ciudad, provincia, lat, lon}]}]; conCarga: pide bultos/volumen.
function htmlPunto(fuentes, conCarga) {
    const opciones = fuentes.filter(f => f.items.length).map(f => `<optgroup label="${esc(f.grupo)}">` + f.items.map(i =>
        `<option value="${esc(i.id)}">${esc(i.alias)} - ${esc(i.calle)} ${esc(i.altura)}${i.lat == null ? ' (sin ubicar)' : ''}</option>`).join('') + '</optgroup>').join('');
    return `<div class="form-group"><select class="form-control pt-sel" onchange="cambioPunto(this)"><option value="">Seleccione...</option>${opciones}<option value="m">Otra dirección (cargar a mano)</option></select></div>
        <div class="pt-manual" style="display:none;">
            <div class="form-group"><label>Nombre / referencia</label><input type="text" class="form-control pt-alias"></div>
            <div style="display:flex; gap:12px;"><div class="form-group" style="flex:2;"><label>Calle <span style="color:var(--danger);">*</span></label><input type="text" class="form-control pt-calle"></div><div class="form-group" style="flex:1;"><label>Altura</label><input type="text" class="form-control pt-altura"></div></div>
            <div style="display:flex; gap:12px;"><div class="form-group" style="flex:1;"><label>Ciudad</label><input type="text" class="form-control pt-ciudad"></div><div class="form-group" style="flex:1;"><label>Provincia</label><input type="text" class="form-control pt-provincia"></div></div>
            <div class="form-group"><label>Ubicación (link de Google Maps o "lat, lon")</label><input type="text" class="form-control pt-ubicacion" placeholder="https://www.google.com/maps/@-34.60,-58.38,15z"></div>
        </div>
        ${conCarga ? `<div style="display:flex; gap:12px;"><div class="form-group" style="flex:1;"><label>Bultos</label><input type="number" class="form-control pt-bultos" value="1" min="1"></div><div class="form-group" style="flex:1;"><label>Volumen (m³)</label><input type="number" class="form-control pt-volumen" value="0" min="0" step="0.01"></div></div>
            <div style="display:flex; gap:12px;"><div class="form-group" style="flex:1;"><label>Entregar desde (opcional)</label><input type="time" class="form-control pt-desde"></div><div class="form-group" style="flex:1;"><label>Entregar hasta (opcional)</label><input type="time" class="form-control pt-hasta"></div></div>` : ''}`;
}
function cambioPunto(sel) { sel.closest('.punto').querySelector('.pt-manual').style.display = sel.value === 'm' ? 'block' : 'none'; }
function leerPunto(box, indice) {
    const sel = box.querySelector('.pt-sel');
    if (!sel.value) { sel.classList.add('is-invalid'); throw new Error('Elija el origen y cada destino.'); }
    let p;
    if (sel.value === 'm') {
        const calle = box.querySelector('.pt-calle');
        if (!calle.value.trim()) { calle.classList.add('is-invalid'); throw new Error('Indique la calle de la dirección cargada a mano.'); }
        const u = leerUbicacion(box.querySelector('.pt-ubicacion'));
        p = { alias: box.querySelector('.pt-alias').value.trim() || calle.value.trim(), calle: calle.value.trim(), altura: box.querySelector('.pt-altura').value.trim(),
              ciudad: box.querySelector('.pt-ciudad').value.trim(), provincia: box.querySelector('.pt-provincia').value.trim(), lat: u.lat, lon: u.lon };
    } else {
        const i = indice[sel.value];
        p = { alias: i.alias, calle: i.calle, altura: i.altura || '', ciudad: i.ciudad || '', provincia: i.provincia || '', lat: i.lat, lon: i.lon };
    }
    const bultos = box.querySelector('.pt-bultos');
    if (bultos) {
        p.bultos = parseInt(bultos.value) || 1; p.volumen_m3 = parseFloat(box.querySelector('.pt-volumen').value) || 0;
        p.desde = box.querySelector('.pt-desde').value || null; p.hasta = box.querySelector('.pt-hasta').value || null;
        if (p.desde && p.hasta && p.desde > p.hasta) { box.querySelector('.pt-hasta').classList.add('is-invalid'); throw new Error('El horario de entrega termina antes de empezar.'); }
    }
    return p;
}
// Direcciones de un cliente y sucursales en el formato de htmlPunto.
function itemsDirecciones(dirs) { return dirs.map(d => ({ id: 'd:' + d.id, alias: d.alias, calle: d.calle, altura: d.altura, ciudad: d.ciudad, provincia: d.provincia, lat: d.latitud, lon: d.longitud })); }
function itemsSucursales(sucs) { return sucs.map(s => ({ id: 's:' + s.id, alias: s.nombre, calle: s.calle, altura: s.altura, ciudad: s.ciudad, provincia: s.provincia, lat: s.latitud, lon: s.longitud })); }

// --- 1.3.0 ---
function hhmm(t) { return t ? String(t).slice(0, 5) : ''; }
function textoVentana(desde, hasta) { return (desde || hasta) ? `${desde ? 'desde ' + hhmm(desde) : ''}${desde && hasta ? ' ' : ''}${hasta ? 'hasta ' + hhmm(hasta) : ''}` : ''; }
const MOTIVOS = { ausente: 'Nadie para recibir', rechazo: 'Rechazado por el destinatario', direccion_erronea: 'Dirección errónea', danado: 'Mercadería dañada', cerrado: 'Cerrado', otro: 'Otro' };
function linkSeguimiento(token) { return window.location.origin + '/seguimiento.html?t=' + encodeURIComponent(token); }
async function copiarSeguimiento(token) {
    const link = linkSeguimiento(token);
    try { await navigator.clipboard.writeText(link); alert('Link de seguimiento copiado: ' + link); }
    catch (e) { prompt('Copie el link de seguimiento:', link); }
}
// Achica una foto del celular (JPEG, lado mayor 1280 px) para mandarla liviana. Devuelve un data URL.
function comprimirImagen(archivo, lado = 1280, calidad = 0.7) {
    return new Promise((ok, mal) => {
        const lector = new FileReader();
        lector.onerror = () => mal(new Error('No se pudo leer la foto.'));
        lector.onload = () => {
            const img = new Image();
            img.onerror = () => mal(new Error('El archivo no es una imagen.'));
            img.onload = () => {
                const escala = Math.min(1, lado / Math.max(img.width, img.height));
                const c = document.createElement('canvas'); c.width = Math.round(img.width * escala); c.height = Math.round(img.height * escala);
                c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
                ok(c.toDataURL('image/jpeg', calidad));
            };
            img.src = lector.result;
        };
        lector.readAsDataURL(archivo);
    });
}
// Estado de la entrega de una parada (badge).
function badgeEntrega(p) {
    if (p.entrega_estado === 'entregada') return '<span class="badge badge-success">Entregada</span>';
    if (p.entrega_estado === 'no_entregada') return `<span class="badge badge-danger" title="${esc(p.entrega_obs || '')}">No entregada: ${esc(MOTIVOS[p.motivo] || p.motivo)}</span>`;
    return '<span class="badge badge-pending">Pendiente</span>';
}
