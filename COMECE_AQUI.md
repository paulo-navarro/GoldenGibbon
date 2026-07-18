# 🦧 COMECE AQUI — O que você precisa entender sobre o GoldenGibbon

> Escrito em 2026-07-08, depois de analisar os dados reais da sua conta.
> Sem jargão de finanças. Quando aparecer um termo técnico, ele vem explicado do lado.

---

## 1. O que aconteceu com o seu dinheiro (a versão curta)

Primeiro, uma boa notícia: **sua conta tem ~$50, e o sistema sabe disso** — o painel principal consulta a Binance direto e mostra o valor certo. Se você viu números absurdos tipo $2.09 no gráfico de evolução do saldo, aquilo é **bug de coleta de histórico** (explicado na seção 4), não dinheiro sumido.

A má notícia: o robô fez **469 trades** em 10 semanas e as operações perderam **~$7.5** — uns 13% da conta. Pouco em valor absoluto, mas o padrão é o que importa:

**O robô não perdeu porque "errou o mercado"**. Ele perdeu para as **taxas**.

### A analogia do pedágio

Toda vez que o robô compra ou vende, a Binance cobra um pedágio de 0,1% do valor. Comprar + vender = ida e volta = **~0,2% de pedágio por trade** (mais um pouco de "escorregada" no preço, que explico abaixo).

Isso parece pouco. Mas o seu robô cruzou esse pedágio **6 vezes por dia, todo dia, por 10 semanas**, com posições de $7. Os trades dele, em média, nem ganharam nem perderam — ficaram no zero a zero. Só que o pedágio foi cobrado em todos. Resultado: uma sangria lenta e constante.

**Moral:** um robô que acerta "mais ou menos" e opera muito, perde. Um robô que acerta "mais ou menos" e opera pouco, empata. Para ganhar, precisa de vantagem real (os traders chamam isso de *edge*) **depois** de pagar os pedágios.

### Os outros dois problemas

1. **Um bug estava fechando trades à força.** 17% das vezes que o robô saiu de uma posição, não foi decisão de estratégia — foi um sistema interno de "conferência de saldo" (reconciliação) que fechou a posição por conta própria. Isso é bug, e está na lista de correção.
2. **A segunda estratégia (mean_reversion) nunca operou.** Nem uma vez em 10 semanas. As condições dela se contradizem — é como exigir que esteja chovendo e o céu esteja limpo ao mesmo tempo. Também está na lista.

---

## 2. Dicionário mínimo (5 termos, prometo)

| Termo | O que significa em português |
|---|---|
| **Win rate** | De cada 100 trades, quantos terminaram no lucro. O seu está em 21% — baixo. Mas atenção: win rate alto não garante lucro (dá pra ganhar 9 centavos 9 vezes e perder 1 real na décima). |
| **Drawdown** | A maior queda do seu saldo do topo até o fundo. Regra de bolso: acima de 20-25% já é sinal de parar e repensar. (O seu não dá pra medir direito ainda — o histórico de saldo está bugado, ver seção 4.) |
| **Slippage ("escorregada")** | Você manda comprar a $100, mas quando a ordem chega, o preço já foi pra $100.10. Essa diferença sai do seu bolso. Quanto menor a posição e mais agitado o mercado, pior. |
| **Edge (vantagem)** | A diferença entre o seu robô e uma moeda jogada pro alto. Se depois de pagar taxas o resultado esperado é positivo, você tem edge. Se não tem edge, operar mais = perder mais rápido. |
| **Profit factor** | Total ganho ÷ total perdido. Acima de 1.0 = lucro. Um sistema saudável fica acima de 1.2-1.5. |

---

## 3. Backtest: o que é, pra que serve, quando usar

### O que é

Um backtest é uma **máquina do tempo de mentira**. Ele pega o histórico real de preços (ex: os últimos 2 anos do Bitcoin) e pergunta: *"se o robô estivesse ligado nessa época, com essas regras, quanto teria ganhado ou perdido?"*

É a única forma de testar uma ideia **sem apostar dinheiro de verdade**. É o laboratório. O live (conta real) é o hospital — você só vai pro hospital depois que o remédio foi testado no laboratório.

### Pra que serve (e pra que NÃO serve)

- ✅ **Serve para reprovar ideias ruins de graça.** Se a estratégia perde dinheiro nos últimos 2 anos de dados, ela não merece 1 centavo real.
- ✅ Serve para comparar: "a versão A é melhor que a versão B?"
- ⚠️ **Não serve como garantia.** Passar no backtest não garante lucro futuro — o mercado muda. Mas *reprovar* no backtest garante quase certamente prejuízo. Ou seja: ele é um filtro de eliminação, não um selo de aprovação.

### O perigo clássico: decorar a prova

Se você ajustar os parâmetros da estratégia até o backtest ficar bonito, você não criou uma estratégia — você **decorou o passado**. Isso se chama *overfitting*. É como um aluno que decorou as respostas da prova do ano passado: nota 10 na prova velha, zero na prova nova.

O antídoto se chama **walk-forward** (o GoldenGibbon já tem isso pronto): divide o histórico em pedaços, ajusta a estratégia num pedaço e testa no pedaço **seguinte**, que ela nunca viu. Se ela continua boa no pedaço que nunca viu, aí sim é sinal de vantagem real.

### Quando usar (a regra prática)

**Antes de QUALQUER mudança ir para a conta real.** Sempre. Sem exceção. O fluxo é:

```
Ideia → Backtest (2+ anos, com taxas ligadas) → passou? → Walk-forward → passou?
     → Paper trading (robô ligado com dinheiro de mentira) por algumas semanas → passou?
     → Só então, live com pouco dinheiro.
```

Se reprovar em qualquer etapa, volta pro início. Cada reprovação é dinheiro que você NÃO perdeu.

### Por que ele quebra a sua interface hoje

Bug conhecido, já mapeado (task 9.1 do `roadmap/phase_9.md`): o backtest hoje roda **dentro do mesmo processo que serve o site**. É como pedir pro caixa do banco resolver uma equação gigante enquanto atende a fila — a fila toda para. Quando o backtest roda, o dashboard, o WebSocket e até o healthcheck engasgam juntos, e parece que tudo quebrou.

A correção vai mover o backtest para um "funcionário dos fundos" (o Celery worker, que já existe no sistema): você pede o backtest, a interface responde na hora "tô rodando, job #123", e avisa quando terminar. **Até essa correção sair, evite rodar backtest pela interface** — se precisar, aceite que o dashboard vai travar uns minutos e volta sozinho.

---

## 4. Por que o painel mostrava $2.09 se a conta tem $50

O sistema tem duas formas de saber seu saldo:

1. **Perguntar pra Binance agora** — isso funciona. A cada 2 minutos um processo consulta a exchange e vê os $50 corretamente. O número grande do painel vem daí.
2. **O diário de bordo** (a tabela de histórico que alimenta o gráfico de evolução) — esse está quebrado de três jeitos ao mesmo tempo: ele anota o saldo **em fatias separadas** (uma por moeda/estratégia, herança de uma versão antiga do sistema) em vez do total da conta; algumas fatias de **treino com dinheiro de mentira** foram anotadas como se fossem reais; e em certos horários ele anota **tudo duas vezes**. Resultado: o gráfico pega uma fatia qualquer (ex: $2.09) e apresenta como se fosse sua conta.

A ironia: o valor certo ($50) é calculado a cada 2 minutos e **jogado fora** sem ser anotado no diário, enquanto os valores errados são gravados religiosamente. A correção é a task 9.11 do plano.

**Tradução prática:** confie no número grande do painel (vem da Binance ao vivo). Desconfie do gráfico de evolução do saldo até a 9.11 ser feita.

---

## 5. O plano (em uma frase por item)

O detalhe técnico está em `roadmap/phase_9.md`. A tradução:

1. **Consertar o backtest** para não derrubar a interface, e passar a guardar todo resultado (hoje ele joga fora).
2. **Consertar o bug da reconciliação** que fecha trades à força.
3. **Ensinar o backtest a simular os limites reais da Binance** (pedido mínimo de $5, arredondamentos) — hoje ele é otimista demais para contas pequenas.
4. **Operar menos**: mudar a estratégia principal do gráfico de 15 minutos para o de 1-4 horas (menos pedágios), tirar a regra de saída que mais perde dinheiro, e reduzir de 34 moedas para 4-6 grandes.
5. **Consertar ou aposentar a mean_reversion** (a estratégia que nunca operou).
6. **Criar o portão de qualidade**: nenhuma estratégia volta pro dinheiro real sem passar no walk-forward com lucro após taxas.
7. **Consertar o diário de bordo do saldo** (o bug da seção 4), para o gráfico de evolução mostrar a verdade.
8. **Freio de emergência automático**: se a conta cair 20% do pico, o robô para sozinho e te avisa. (Depende do item 7 — não dá pra frear pelo velocímetro quebrado.)

## 6. O que EU (você, humano) preciso decidir e fazer

- **Não colocar mais dinheiro agora.** Primeiro o plano acima, depois paper trading, depois — só se tudo passar — dinheiro de verdade.
- **Aceitar que a resposta do laboratório pode ser "essa estratégia não presta".** Isso é o sistema funcionando, não falhando. Você disse: prefere estar errado a perder dinheiro. O backtest é exatamente a máquina de estar errado de graça.
- **Quando religar o live, ligar com pouco** e com o freio automático do item 7 ativo.
- **Desconfiar de qualquer resultado bonito demais.** Se um backtest der +300%, a primeira hipótese é bug ou overfitting, não genialidade.

---

*Arquivos relacionados: `roadmap/phase_9.md` (plano técnico) · `README.md` (visão geral da plataforma) · `roadmap/kanban.md` (histórico de desenvolvimento)*
