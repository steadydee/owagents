# SIRE Form Map Recon Checklist

Live SIRE browser automation is intentionally blocked until this checklist is
completed against official SIRE pages in a real recon session.

Record:

- login URL and authentication steps
- city code field source and accepted value for Owl's Watch
- Codigo de empresa value source
- entrada form fields, labels, selectors, and required/optional status
- salida form fields, labels, selectors, and required/optional status
- success receipt page fields and how to capture receipt references
- duplicate/idempotency behavior for repeated entrada or salida attempts
- failure messages for invalid document data, expired sessions, and city code issues

## Public Recon Findings

Official public page:
`https://portal.migracioncolombia.gov.co/tramites-y-servicios/aplicativos/registro-sire`

Official portal login:
`https://apps.migracioncolombia.gov.co/sire/public/login.jsf`

Official public registration path:
`https://apps.migracioncolombia.gov.co/sire/public/solicitarCuentaUsuario.jsf`

Public SIRE page states the service is virtual, free, and has a `3 dias
habiles` processing time for the registration response. The login form requires
tipo de documento, numero de documento, password, and a selected reporting
person/legal entity. Account registration is required before live automation.

Owl's Watch public SIRE constants discovered from the registration form:

- Tipo de reporte: `Alojamiento y Hospedaje`
- Departamento: `CALDAS`, value `17`
- Municipio: `MANIZALES`, value `17001`. Source trail: RNT/chamber context and
  the supplied RUT establishment page for `OWL'S WATCH`.
- Actividad economica: `Alojamiento rural. (5514)`, value `5514`

Pre-login account registration form ids discovered from the public page:

- `solicitarCuentaUsuario:tipoPersona`: values `1` persona juridica, `2`
  persona natural con actividad comercial, `3` persona natural.
- `solicitarCuentaUsuario:nit`, `solicitarCuentaUsuario:dv`, and
  `solicitarCuentaUsuario:cedula`.
- `solicitarCuentaUsuario:tipoEmpresaSOM`: economic activity, value `5514`.
- `solicitarCuentaUsuario:nombreEmpresa` and
  `solicitarCuentaUsuario:naturaleza`, where private is value `R`.
- `solicitarCuentaUsuario:email` and
  `solicitarCuentaUsuario:fechaNacimientoInputDate` for company start date.
- `solicitarCuentaUsuario:nombreRepresentante`,
  `solicitarCuentaUsuario:tipoDocEmpresas`, and
  `solicitarCuentaUsuario:numeroDocumento`.
- `solicitarCuentaUsuario:upload:file` plus the `Adjuntar Documento` button.
- `solicitarCuentaUsuario:direccion`, `fax`, `telefono`, and
  `telefonomovil`.
- `solicitarCuentaUsuario:departamentos`, `ciudades`, `corregimientos`, and
  `descripcionAdicional`.
- Responsible-person fields:
  `solicitarCuentaUsuario:tipoDocumentoResponsable`,
  `numeroDocumentoRepresentante`, `primerApellidoSolicitanteRepresentante`,
  `segundoApellidonombreSolicitanteRepresentante`, `nombreSolicitante`,
  `cargo`, `movil`, `generoResponsable`, `fechaNacInputDate`,
  `nacionalidadResponsable`, `emailSolicitanteRepresentante`, and
  `emailSolicitanteRepresentanteConfirmacion`.
- `solicitarCuentaUsuario:secureText` is the captcha input and final
  registration submit is `solicitarCuentaUsuario:j_id192`.

Still recon-gated:

- Codigo de empresa. This is not visible before account approval/login.
- Whether any SIRE post-login screen asks for taxpayer domicilio instead of
  establishment location. If it does, use the supplied RUT taxpayer-location
  page for that specific field only.
- SIRE credentials and approved reporting person/legal entity selector value.
- Entrada/salida post-login form selectors, required fields, receipt shape,
  duplicate behavior, and error messages.
- Whether the account registration captcha/manual approval flow must be
  completed by Dennis before automation can observe post-login pages.

After recon, add a separate SIRE browser routine PR. Do not wire live SIRE
submission into the current Registro processing skill.
