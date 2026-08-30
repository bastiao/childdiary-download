# ChildDiary Download

Descarregue para o seu computador as fotos e os vídeos do seu filho que estão
no [ChildDiary](https://app.childdiary.net) (a plataforma usada por muitos
infantários e creches em Portugal).

A aplicação permite ver a galeria, mas guardar tudo é um trabalho manual,
foto a foto. Este projeto automatiza esse processo: navega-se a galeria uma
vez no browser, exporta-se um ficheiro `.har`, e o script trata do resto —
descarrega tudo, organiza por mês e cria um índice em CSV.

> **Este projeto foi programado com IA (Claude, da Anthropic).**
> Foi escrito para resolver um problema concreto de um pai que queria ter as
> fotos do filho guardadas em casa, e é partilhado na esperança de ajudar
> outros pais na mesma situação. Leia o código antes de o correr — são cerca
> de 500 linhas de Python, sem dependências externas, e não envia nada para
> lado nenhum.

---

## O que vai precisar

- **Python 3.8 ou superior.** No Windows instale a partir de
  [python.org](https://www.python.org/downloads/) (marque a opção
  *"Add Python to PATH"*). No macOS e Linux já vem instalado.
- **Um browser** (Chrome, Edge ou Firefox) com sessão iniciada no ChildDiary.
- Nada mais. O script usa apenas a biblioteca padrão do Python — não é preciso
  instalar pacotes.

---

## Passo 1 — Exportar o HAR da galeria

Um ficheiro **HAR** é simplesmente uma gravação de tudo o que o browser pediu
ao servidor enquanto esteve a navegar. Como a galeria carrega as fotos à
medida que se percorre a página, basta percorrê-la toda uma vez e o HAR fica
com todos os endereços das fotos e vídeos lá dentro.

### No Chrome ou Edge

1. Inicie sessão em <https://app.childdiary.net> e abra a **galeria** do seu filho.
2. Carregue em `F12` para abrir as Ferramentas de Programador
   (ou menu → Mais ferramentas → Ferramentas de programador).
3. Escolha o separador **Network** / **Rede**.
4. Ligue a opção **Preserve log** / **Preservar registo** (a caixa no topo).
   É importante: sem isto o registo é apagado sempre que muda de página.
5. **Percorra a galeria toda.** Faça scroll até ao fim, clique em *"Ver mais"*
   / *"Carregar mais"* as vezes que forem precisas, até chegar à data mais
   antiga que quer guardar. Dê tempo às fotos para aparecerem no ecrã.
6. Clique com o botão direito em qualquer linha da lista de pedidos e escolha
   **"Save all as HAR with content"** / **"Guardar tudo como HAR com conteúdo"**.

   ⚠️ Tem mesmo de ser a opção **com conteúdo** (*with content*). A outra opção
   guarda só a lista de endereços e o script não consegue ler as datas.
7. Guarde o ficheiro na mesma pasta do script, por exemplo `galeria.har`.

### No Firefox

Igual, mas no passo 6: separador **Rede** → ícone de engrenagem (canto
superior direito do painel) → **"Guardar tudo como HAR"**.

> O ficheiro `.har` pode ficar grande (dezenas ou centenas de MB). É normal:
> ele contém as próprias imagens que o browser já tinha carregado.
> **Contém também os seus dados de sessão — não o partilhe com ninguém.**

---

## Passo 2 — Ver o que foi encontrado

Antes de descarregar seja o que for, veja o que o script consegue identificar:

```bash
python childdiary_download.py galeria.har --list
```

Vai obter algo assim:

```
Reading 1 file(s)...
  galeria.har: 412 candidate URL(s)

387 unique media URL(s) found.
  102 skipped as thumbnails (use --keep-thumbs to include)
  31 outside the date range
  -> 254 to download (231 photos, 23 videos, 0 unknown)

  2025-09-03  photo    https://app.childdiary.net/api/resource/...
  ...
```

Se aparecerem **0 URLs**, veja a secção *Problemas frequentes* mais abaixo.

---

## Passo 3 — Descarregar

```bash
python childdiary_download.py galeria.har -o fotos
```

As fotos e vídeos ficam na pasta `fotos/`, organizados em subpastas por mês
(`2025-09/`, `2025-10/`, …), com o nome no formato
`DATA_titulo_nomeoriginal_id.jpg`.

É criado também um `fotos/manifest.csv` com a lista completa: data, tipo,
ficheiro, tamanho, estado e endereço original. Pode voltar a correr o comando
sem problema — o que já foi descarregado é saltado.

Por omissão só são descarregados os ficheiros a partir de **1 de setembro de
2025**. Para outro intervalo, use `--since` (ver *Opções*).

### Se der erro de autenticação

Alguns endereços só funcionam com sessão iniciada. Nesse caso o script
escreve `HTTP 401/403 (auth needed)` ou `got HTML (login required)`.
Precisa de lhe dar o seu *cookie*:

1. Nas Ferramentas de Programador, separador **Network**, clique num pedido
   qualquer para `app.childdiary.net`.
2. Botão direito → **Copy** → **Copy as cURL**.
3. Cole num editor de texto e procure a parte `-H 'Cookie: ...'`.
   Copie **apenas** o que vem depois de `Cookie: `.
4. Corra:

```bash
python childdiary_download.py galeria.har -o fotos --cookie "COLE_AQUI_O_COOKIE"
```

Ou, para não deixar o cookie no histórico da linha de comandos, guarde-o num
ficheiro e use `--cookie-file cookie.txt`.

> O cookie é a sua sessão. Expira ao fim de algum tempo e **não deve ser
> partilhado nem colocado no Git** (o `.gitignore` já o protege).

---

## Opções

| Opção | O que faz |
|---|---|
| `--since AAAA-MM-DD` | Data mais antiga a descarregar. Por omissão `2025-09-01`. |
| `--until AAAA-MM-DD` | Data mais recente a descarregar. |
| `-o`, `--out PASTA` | Pasta de destino (por omissão `childdiary_media`). |
| `--list` | Mostra o que seria descarregado, sem descarregar nada. |
| `--flat` | Guarda tudo numa única pasta, sem subpastas por mês. |
| `--keep-thumbs` | Inclui também as miniaturas (por omissão são ignoradas). |
| `--no-date-only` | Ignora os ficheiros a que não foi possível associar uma data. |
| `--cookie` / `--cookie-file` | Cookie de sessão, se for necessário. |
| `-H "Nome: valor"` | Cabeçalho HTTP adicional (repetível). |
| `--delay SEGUNDOS` | Pausa entre downloads (por omissão `0.3`), para não sobrecarregar o servidor. |

Exemplo — todo o ano letivo, tudo na mesma pasta:

```bash
python childdiary_download.py galeria.har -o fotos --since 2025-09-01 --until 2026-07-31 --flat
```

Lista completa das opções: `python childdiary_download.py --help`

---

## Problemas frequentes

**"0 URLs encontrados"**
Quase sempre é o HAR: foi guardado *sem* conteúdo, ou a galeria não chegou a
ser percorrida até ao fim. Repita o Passo 1 com atenção ao *"with content"* e
ao *"Preserve log"*.

**"got HTML (login required or bad URL)"**
A sessão é obrigatória para esses endereços — use `--cookie` (ver acima). Se
já usou, o cookie provavelmente expirou; copie um novo.

**Faltam fotos antigas**
O HAR só regista o que o browser chegou a pedir. Se não percorreu a galeria
até setembro, essas fotos não estão lá. Volte a exportar depois de carregar
mais páginas.

**Aparecem muitas miniaturas em vez das fotos grandes**
O script tenta ignorar miniaturas automaticamente. Se ficou com poucas fotos,
experimente `--keep-thumbs` para ver tudo o que existe.

**`python` não é reconhecido (Windows)**
Experimente `py childdiary_download.py ...` ou reinstale o Python com a opção
*"Add Python to PATH"* marcada.

---

## Alternativa: guardar as respostas JSON à mão

Se preferir não exportar o HAR, pode copiar as respostas da API uma a uma
(Network → botão direito no pedido → *Copy* → *Copy response*), guardá-las em
`./json/pagina1.json`, `pagina2.json`, … e passar a pasta ao script:

```bash
python childdiary_download.py ./json --list
```

No fim do próprio `childdiary_download.py` há ainda um pequeno excerto de
JavaScript para descarregar todas as páginas da API de uma vez pela consola.

---

## Privacidade

- Tudo corre **no seu computador**. O script fala apenas com o
  `app.childdiary.net` para ir buscar os ficheiros — não há servidores
  intermediários, nem telemetria, nem recolha de dados.
- **Nunca publique o seu `.har`, o `cookie.txt` ou as fotos.** O `.gitignore`
  deste repositório já exclui esses ficheiros, mas confirme sempre antes de
  fazer *commit*.
- Descarregue apenas conteúdo do seu próprio filho, a que já tem acesso
  legítimo enquanto encarregado de educação. As fotos podem incluir outras
  crianças — trate-as com o mesmo cuidado com que gostaria que tratassem as do
  seu filho, e não as divulgue.

---

## Contribuir

Este projeto não tem qualquer ligação ao ChildDiary nem à empresa que o
desenvolve. É um utilitário feito por um pai, para pais.

Se a aplicação mudar e o script deixar de funcionar, ou se conseguir
melhorá-lo, abra um *issue* ou um *pull request*. Ao reportar um problema,
**não anexe o seu ficheiro HAR** — descreva antes o que aconteceu e cole a
saída do `--list`, sem os endereços completos.

## Licença

MIT — ver [LICENSE](LICENSE). Sem qualquer garantia: use por sua conta e risco.
