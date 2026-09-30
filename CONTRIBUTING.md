# Contribuir a JZTravell

Gracias por el interés. Este proyecto es parte de JZTech Suite y se distribuye bajo
**AGPLv3** (ver `LICENSE`): tu contribución se publica bajo los mismos términos.

## Certificado de Origen del Desarrollador (DCO)

Todo commit tiene que llevar la línea `Signed-off-by` (se agrega sola con `git commit -s`).
Con ella certificás lo siguiente:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

Texto original: <https://developercertificate.org/>.

## Reglas del proyecto

1. **Seguridad y estabilidad primero.** Denegar por defecto: toda ruta nueva exige sesión
   salvo que se marque pública a propósito.
2. **Un cambio por commit, probado.** No mezclar un arreglo de seguridad con un refactor.
3. **Minimalismo.** Pocas dependencias, versiones fijadas (con hash cuando aplica). Todo
   paquete nuevo se justifica.
4. **Hardware modesto.** Tiene que correr en una PC de oficina común.
5. **Errores:** nunca `except: pass` ni devolver `str(e)` al cliente. Se registra el detalle
   en el servidor y al usuario se le muestra un mensaje genérico.
6. **Interfaz sin emojis:** íconos solo en SVG.
7. Cada cambio tiene que estar probado antes de abrir un PR.

## Reportar una vulnerabilidad

No abras un issue público. Escribí por privado al mantenedor (perfil de GitHub
[@Jonnyonz](https://github.com/Jonnyonz)) con los pasos para reproducirla.
