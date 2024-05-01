import os
import sys
import pytz
import wandb
import logging
import datetime


from .plot import *
from .metrics import *
from tqdm import tqdm


class TqdmLoggingHandler(logging.StreamHandler):
    """Avoid tqdm progress bar interruption by logger's output to console"""
    # see logging.StreamHandler.eval method:
    # https://github.com/python/cpython/blob/d2e2534751fd675c4d5d3adc208bf4fc984da7bf/Lib/logging/__init__.py#L1082-L1091
    # and tqdm.write method:
    # https://github.com/tqdm/tqdm/blob/f86104a1f30c38e6f80bfd8fb16d5fcde1e7749f/tqdm/std.py#L614-L620

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg, end=self.terminator)
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)

class Logger():
    def __init__(self, args, md):
        self.wandb = args.wandb
        self.project = args.project
        self.molecule = args.molecule
        self.start_file = md.start_file
        self.type = args.type
        if self.type == "train":
            self.num_rollouts = args.num_rollouts
            self.save_freq = args.save_freq
            self.num_samples = args.num_samples
        else:
            self.save_freq = 1
            
        self.seed = args.seed
        if not hasattr(args, 'date'):
            raise ValueError("Date is not provided in args")
        self.date = args.date
        kst = pytz.timezone('Asia/Seoul')
        
        self.dir = f'results/{self.molecule}/{self.project}/{self.date}/{self.type}/{args.std_scale}'
        
        # Set up system logging    
        if args.logger:
            # Check directories
            if not os.path.exists(self.dir):
                os.makedirs(self.dir)
            if self.type == "train":
                for dir in ['policy', 'potential']:
                    if not os.path.exists(f'{self.dir}/{dir}'):
                        os.makedirs(f'{self.dir}/{dir}')
            
            # Logger basic configurations
            log_file_name = self.dir + f"/{self.type}.log"
            self.logger = logging.getLogger("tps")
            self.logger.setLevel(logging.INFO)
            
            # File handler
            file_handler = logging.FileHandler(log_file_name, mode="w")
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
            
            # Console handler
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter('%(levelname)s: %(message)s')
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
            self.logger.propagate = False
            
            # tqdm handler
            # self.logger.addHandler(TqdmLoggingHandler())
            
            log_date = datetime.datetime.now(tz=kst).strftime("%Y-%m-%d %H:%M:%S")
            self.logger.info(f"Date: {log_date}")
            self.logger.info(f"Logging {self.type} {self.molecule}, seed {self.seed}")
            self.logger.info("")

    def info(self, message):
        if self.logger:
            self.logger.info(message)
    
    def log(self, loss, policy, rollout, positions, last_position, target_position, potentials, log_target_reward, log_reward, last_idx):
        epd = expected_pairwise_distance(last_position, target_position)
        if self.molecule == 'alanine':
            thp = target_hit_percentage(last_position, target_position)
            etp = energy_transition_point(last_position, target_position, potentials, last_idx)

        # In case of training logger
        if self.type == "train":
            # Save policy at save_freq and last rollout
            if rollout % self.save_freq == 0:
                torch.save(policy.state_dict(), f'{self.dir}/policy/policy_{rollout}.pt')
                torch.save(policy.state_dict(), f'{self.dir}/policy.pt')
            if rollout == self.num_rollouts - 1 :
                torch.save(policy.state_dict(), f'{self.dir}/policy.pt')
            
            # Log potential by trajectory index, with termianl reward, log reward
            if rollout % self.save_freq == 0:
                self.logger.info(f"Plotting potentials for {self.num_samples} samples...")
                fig_potential = plot_potentials(
                    self.dir+"/potential",
                    rollout,
                    potentials,
                    log_target_reward,
                    log_reward,
                    last_idx
                )
                self.logger.info(f"Plotting Done.!!")
        
        # Log to wandb
        if self.wandb:
            if self.molecule == 'alanine':
                log = {
                        'expected_pairwise_distance (pm)': epd,
                        'target_hit_percentage (%)': thp,
                        'energy_transition_point (kJ/mol)': etp,
                        'loss': loss,
                    }
            else:
                log = {
                        'expected_pairwise_distance (pm)': epd,
                        'loss': loss,
                    }
            wandb.log(log, step=rollout)

            if rollout % self.save_freq==0 and self.molecule == 'alanine':
                fig_potential = f"{self.dir}/potential/potential_rollout{rollout}.png"
                wandb.log(
                    {
                        'paths': wandb.Image(plot_paths_alanine(positions, target_position, last_idx)),
                        'potentials': wandb.Image(fig_potential),
                    }, 
                    step=rollout
                )

        # Log to system log
        if self.logger:
            self.logger.info("")
            self.logger.info(f'Rollout: {rollout}')
            self.logger.info(f"expected_pairwise_distance (pm): {epd}")
            if self.type == "train":
                self.logger.info(f"Loss: {loss}")
            if self.molecule == 'alanine':
                self.logger.info(f"target_hit_percentage (%): {thp}")
                self.logger.info(f"energy_transition_point (kJ/mol): {etp}")
    
    def plot(self, positions, target_position, potentials, log_target_reward, log_reward, last_idx, **kwargs):
        self.logger.info(f"[Plot] Plotting potentials")
        plot_potential(self.dir, potentials, log_target_reward, log_reward, last_idx)
        
        self.logger.info(f"[Plot] Plotting 3D view")
        plot_3D_view(self.dir, self.start_file, positions, last_idx)
        
        if self.molecule == 'alanine':
            self.logger.info(f"[Plot] Plotting paths")
            plot_path(self.dir, positions, target_position, last_idx)