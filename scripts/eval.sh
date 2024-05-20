project=poly-mode
date=0517

# for seed in {0..1}
# for seed in {0..7}
for seed in 0
do
  echo ">>" Evaluating poly for seed $seed
  CUDA_VISIBLE_DEVICES=$seed python src/eval.py \
    --molecule poly \
    --project $project \
    --date $date \
    --seed $seed \
    --num_samples 16 \
    --num_steps 5000 \
    --bias_scale 0.01 \
    --flexible \
    --wandb &
    sleep 1
done

wait