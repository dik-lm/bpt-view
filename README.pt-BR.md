# BPT-View

Viewer leve em Python para arquivos de tomografia `.BPT`, com suporte a:

- MPR axial, coronal e sagital
- MIP frontal
- reconstrução panorâmica curva (curved MPR)
- ajuste de brilho/contraste
- ajuste visual do aspecto em Z
- divisórias móveis entre os painéis
- exportação dos cortes renderizados

## Funcionalidades

- Parser estruturado para `.BPT`
- Carregamento assíncrono com UI responsiva
- Visualizações axial / coronal / sagital
- Visualização MIP frontal
- Reconstrução panorâmica curva a partir de spline desenhada pelo usuário
- Toggle das linhas-guia
- Exportação individual de cortes (axial, coronal, sagital)
- Controles de window/level
- Painéis redimensionáveis

## Requisitos

Instale as dependências com:

```bash
pip install numpy imagecodecs Pillow scipy
````

Ou usando `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Uso

Executar sem argumentos para abrir o seletor de arquivo:

```bash
python bpt_viewer.py
```

Executar com o caminho de um arquivo `.BPT`:

```bash
python bpt_viewer.py caminho/para/exame.bpt
```

## Sobre o formato `.BPT`

Este projeto inclui um parser para o layout do container `.BPT` observado durante o desenvolvimento.

Estrutura observada:

* header de 64 bytes (`16 × uint32`, little-endian)
* primeira fatia JPEG sem prefixo de tamanho
* fatias seguintes armazenadas como:

  * `[uint32 little-endian tamanho]`
  * `[payload JPEG]`
* possível bloco final não documentado após a última fatia

Campos mapeados no header:

|  Índice | Campo                         | Tipo         |
| ------: | ----------------------------- | ------------ |
|  `h[6]` | `width`                       | `uint32`     |
|  `h[7]` | `height`                      | `uint32`     |
|  `h[8]` | `num_slices`                  | `uint32`     |
|  `h[9]` | `spacing_x`                   | `float32 LE` |
| `h[10]` | `spacing_y`                   | `float32 LE` |
| `h[11]` | `spacing_z`                   | `float32 LE` |
| `h[15]` | comprimento da primeira fatia | `uint32`     |

## Notas técnicas

### Geometria de display

O render e o mapeamento de clique usam a mesma função de geometria, o que mantém a interação consistente entre os painéis.

### Consistência da MIP

A MIP frontal segue a mesma convenção interna de display usada pelas views coronal e sagital, mantendo crosshair e click mapping alinhados.

### Reconstrução panorâmica curva

A panorâmica é reconstruída a partir de uma spline desenhada na axial e amostrada em espaço físico, usando slab MIP interpolado ao longo da curva.

### Aspecto em Z

O viewer expõe um controle visual de aspecto em Z para display.
O preset inicial de exibição é `1.5×`, enquanto o aspecto físico derivado do spacing continua sendo acompanhado internamente.

## Limitações conhecidas

* A implementação se baseia na estrutura `.BPT` observada neste projeto. Outras variantes de `.BPT` podem não funcionar.
* A reconstrução panorâmica roda na thread da interface e pode travar brevemente a UI em volumes maiores.
* A janela panorâmica não é atualizada automaticamente quando o window/level muda; é necessário recalcular.
* Não há ferramentas de medição de distância.
* Não há atalhos de teclado implementados no momento.

## Aviso importante

Este projeto é um viewer pessoal/técnico para arquivos de tomografia `.BPT`.

Ele:

* não é uma estação médica certificada
* não se destina a diagnóstico clínico oficial
* não substitui software médico homologado

## Licença

MIT

## English version

Para a versão em inglês, veja:

`README.md`
