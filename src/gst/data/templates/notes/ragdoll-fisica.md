# {{title}}

> Jogo: {{game}} ({{engine}}, build {{build}}). Nota `{{note}}`, criada em {{date}} com o template `ragdoll-fisica`.
> Pré-preenchida de: {{sources}}.
> Evidência: `caminho:linha` (decomp/, assets/), chave de `analysis/<x>.json` (`joints.json`, `godot_animation.json` `data.ragdolls`).
> Legenda: **[E]** lido no código ou nos dados; **[I]** inferência. O que não foi confirmado vai para "Não verificado".

## 1. Resposta curta

<!-- preencher: tipo de corpo (active ragdoll por pose matching, ragdoll só na morte, corpo rígido único, procedural/IK, sem ragdoll) e a tese em 3-5 linhas. -->

## 2. Montagem do corpo

{{auto:ragdoll}}

<!-- preencher: partes (ossos/corpos), componentes de cada parte (Rigidbody/PhysicalBone, colisores, juntas), massas, camadas de colisão. -->

## 3. Ordem de execução

<!-- preencher: o que roda em FixedUpdate/_physics_process e em que ordem (animação -> pose alvo -> forças -> correção). -->

## 4. Controle do corpo

<!-- preencher: como a física persegue a animação (spring/damper de juntas, forças, torques), com fórmulas e constantes. -->

## 5. Entrar e sair do ragdoll

<!-- preencher: gatilhos (queda, dano, morte, ação), escalar de controle, como volta a ficar de pé; stateDiagram-v2. -->

## 6. Parâmetros

{{auto:tunables}}

## 7. Rede

<!-- preencher: quem simula o corpo (dono, host, todos) e o que é replicado. -->

## 8. Relevância para o meu jogo

<!-- preencher: o que dá a sensação cômica ou física, custo de reproduzir, valores iniciais. -->

## Não verificado

<!-- preencher -->
-
