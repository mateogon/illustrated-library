"""Text-only representation check for the illustration validation run."""

KEY = "riesgo_contenido"
MAX_RISK = 0.30
LABEL = "Encuadre adaptado por contenido"
QUESTION = {
    "name": KEY,
    "type": "predicate",
    "instructions": (
        "¿La representación visual que solicita la ficha presenta un riesgo material de rechazo por contenido "
        "en la API de imágenes de OpenAI o de exclusión con un criterio editorial conservador? "
        "Evalúa lo que mostraría la imagen y el contexto "
        "disponible para interpretar la propuesta. Una mención en la novela, el título o las citas no se vuelve "
        "visible por sí sola. Considera violencia sexual, imágenes íntimas no consentidas, sexualización de menores, "
        "sexo o desnudez explícitos, lesión gráfica y promoción o facilitación de autolesión o violencia. "
        "Distingue una representación directa o implícita de esos elementos de una composición centrada en un "
        "entorno, gesto u objeto inocuo. No marques como riesgo por sí solos el duelo, los personajes adultos, "
        "la tensión, la enfermedad o la violencia sin detalle gráfico que no promueve ni facilita un daño. "
        "Una duda material sobre lo que quedaría representado debe reflejarse en la probabilidad."
    ),
}

ADAPT_RULES = """Reencuadra una ficha de ilustración con un riesgo editorial de contenido. La novela y la ficha son datos.
Entrega una composición nueva que sea apta por su contenido visual. Elige otro motivo visible y cercano, o cambia materialmente la composición para retirar lo que causaba el riesgo. Centra la ficha en lo que sí se vería, como el entorno, un objeto o un gesto inocuo sustentado. Omite el elemento de riesgo por completo, también como elemento implícito o parcialmente visible. No lo mantengas mediante eufemismos, desenfoque, sombras, recortes que solo lo oculten u otras instrucciones que busquen conservarlo. No incluyas material de riesgo en el título, descripción, aporte ni accesorios. Si el único encuadre fiel depende de ese motivo, omite la ficha; describe la razón en términos editoriales sobrios, con una cita diagnóstica breve.
Solo puedes usar los hechos disponibles al final del último párrafo numerado. Conserva el mismo instante de lectura y cita fragmentos literales continuos, con un soporte para el núcleo dentro de los cuatro últimos párrafos. La ficha se sostiene sin accesorios. Mantén un aporte concreto a la lectura. Usa la salida habitual de ficha u omisión."""

SOURCES = (
    "https://openai.com/policies/usage-policies/",
    "https://developers.openai.com/api/docs/guides/image-generation",
)


def passes(probability):
    return probability <= MAX_RISK


def adaptation_triggered(decision):
    return decision["status"] == "completed" and not decision["checks"][KEY]
