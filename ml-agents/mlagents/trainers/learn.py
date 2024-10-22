# # Unity ML-Agents Toolkit
import yaml

import os
import numpy as np
import json

from typing import Callable, Optional, List, Dict

from copy import deepcopy

import mlagents.trainers
import mlagents_envs
from mlagents import tf_utils
from mlagents.trainers.models import ScheduleType
from mlagents.trainers.trainer_controller import TrainerController
from mlagents.trainers.meta_curriculum import MetaCurriculum
from mlagents.trainers.trainer_util import TrainerFactory, handle_existing_directories
from mlagents.trainers.stats import (
    TensorboardWriter,
    CSVWriter,
    StatsReporter,
    GaugeWriter,
    ConsoleWriter,
)
from mlagents.trainers.cli_utils import parser
from mlagents_envs.environment import UnityEnvironment
from mlagents.trainers.sampler_class import SamplerManager
from mlagents.trainers.exception import SamplerException
from mlagents.trainers.settings import RunOptions
from mlagents.trainers.training_status import GlobalTrainingStatus
from mlagents_envs.base_env import BaseEnv
from mlagents.trainers.subprocess_env_manager import SubprocessEnvManager
from mlagents_envs.side_channel.side_channel import SideChannel
from mlagents_envs.side_channel.engine_configuration_channel import EngineConfig
from mlagents_envs.timers import (
    hierarchical_timer,
    get_timer_tree,
    add_metadata as add_timer_metadata,
)
from mlagents_envs import logging_util

logger = logging_util.get_logger(__name__)

TRAINING_STATUS_FILE_NAME = "training_status.json"


def get_version_string() -> str:
    # pylint: disable=no-member
    return f""" Version information:
  ml-agents: {mlagents.trainers.__version__},
  ml-agents-envs: {mlagents_envs.__version__},
  Communicator API: {UnityEnvironment.API_VERSION},
  TensorFlow: {tf_utils.tf.__version__}"""


def parse_command_line(argv: Optional[List[str]] = None) -> RunOptions:
    args = parser.parse_args(argv)
    return RunOptions.from_argparse(args)


def run_training(run_seed: int, options: RunOptions) -> None:
    """
    Launches training session.
    :param options: parsed command line arguments
    :param run_seed: Random seed used for training.
    :param run_options: Command line arguments for training.
    """

    options.checkpoint_settings.run_id = "test8"

    with hierarchical_timer("run_training.setup"):
        checkpoint_settings = options.checkpoint_settings
        env_settings = options.env_settings
        engine_settings = options.engine_settings
        base_path = "results"
        write_path = os.path.join(base_path, checkpoint_settings.run_id)
        maybe_init_path = (
            os.path.join(base_path, checkpoint_settings.initialize_from)
            if checkpoint_settings.initialize_from
            else None
        )
        run_logs_dir = os.path.join(write_path, "run_logs")
        port: Optional[int] = env_settings.base_port
        # Check if directory exists
        handle_existing_directories(
            write_path,
            checkpoint_settings.resume,
            checkpoint_settings.force,
            maybe_init_path,
        )
        # Make run logs directory
        os.makedirs(run_logs_dir, exist_ok=True)
        # Load any needed states
        if checkpoint_settings.resume:
            GlobalTrainingStatus.load_state(
                os.path.join(run_logs_dir, "training_status.json")
            )
        # Configure CSV, Tensorboard Writers and StatsReporter
        # We assume reward and episode length are needed in the CSV.
        csv_writer = CSVWriter(
            write_path,
            required_fields=[
                "Environment/Cumulative Reward",
                "Environment/Episode Length",
            ],
        )
        tb_writer = TensorboardWriter(
            write_path, clear_past_data=not checkpoint_settings.resume
        )
        gauge_write = GaugeWriter()
        console_writer = ConsoleWriter()
        StatsReporter.add_writer(tb_writer)
        StatsReporter.add_writer(csv_writer)
        StatsReporter.add_writer(gauge_write)
        StatsReporter.add_writer(console_writer)

    engine_config = EngineConfig(
        width=engine_settings.width,
        height=engine_settings.height,
        quality_level=engine_settings.quality_level,
        time_scale=engine_settings.time_scale,
        target_frame_rate=engine_settings.target_frame_rate,
        capture_frame_rate=engine_settings.capture_frame_rate,
    )
    if env_settings.env_path is None:
        port = None
    # Begin training

    env_settings.env_path = "C:/Users/Sebastian/Desktop/RLUnity/Training/mFindTarget_new/RLProject.exe"
    env_factory = create_environment_factory(
        env_settings.env_path,
        engine_settings.no_graphics,
        run_seed,
        port,
        env_settings.env_args,
        os.path.abspath(run_logs_dir),  # Unity environment requires absolute path
    )
    env_manager = SubprocessEnvManager(
        env_factory, engine_config, env_settings.num_envs
    )

    maybe_meta_curriculum = try_create_meta_curriculum(
        options.curriculum, env_manager, restore=checkpoint_settings.resume
    )
    sampler_manager, resampling_interval = create_sampler_manager(
        options.parameter_randomization, run_seed
    )
    max_steps = options.behaviors['Brain'].max_steps
    options.behaviors['Brain'].max_steps = 10

    trainer_factory = TrainerFactory(
        options,
        write_path,
        not checkpoint_settings.inference,
        checkpoint_settings.resume,
        run_seed,
        maybe_init_path,
        maybe_meta_curriculum,
        False,
        total_steps=0
    )
    trainer_factory.trainer_config['Brain'].hyperparameters.learning_rate_schedule = ScheduleType.CONSTANT

    # Create controller and begin training.
    tc = TrainerController(
        trainer_factory,
        write_path,
        checkpoint_settings.run_id,
        maybe_meta_curriculum,
        not checkpoint_settings.inference,
        run_seed,
        sampler_manager,
        resampling_interval,
    )
    try:
        # Get inital weights
        tc.init_weights(env_manager)
        inital_weights = deepcopy(tc.weights)
    finally:
        env_manager.close()
        write_run_options(write_path, options)
        write_timing_tree(run_logs_dir)
        write_training_status(run_logs_dir)

    options.behaviors['Brain'].max_steps = max_steps
    step = 0
    counter = 0
    max_meta_updates = 200
    while counter < max_meta_updates:
        sample = np.random.random_sample()
        if(sample > 1):
            print("Performing Meta-learning on Carry Object stage")
            env_settings.env_path = "C:/Users/Sebastian/Desktop/RLUnity/Training/mCarryObject_new/RLProject.exe"
        else:
            print("Performing Meta-learning on Find Target stage")
            env_settings.env_path = "C:/Users/Sebastian/Desktop/RLUnity/Training/mFindTarget_new/RLProject.exe"

        env_factory = create_environment_factory(
            env_settings.env_path,
            engine_settings.no_graphics,
            run_seed,
            port,
            env_settings.env_args,
            os.path.abspath(run_logs_dir),  # Unity environment requires absolute path
        )

        env_manager = SubprocessEnvManager(
            env_factory, engine_config, env_settings.num_envs
        )

        maybe_meta_curriculum = try_create_meta_curriculum(
            options.curriculum, env_manager, restore=checkpoint_settings.resume
        )
        sampler_manager, resampling_interval = create_sampler_manager(
            options.parameter_randomization, run_seed
        )

        trainer_factory = TrainerFactory(
            options,
            write_path,
            not checkpoint_settings.inference,
            checkpoint_settings.resume,
            run_seed,
            maybe_init_path,
            maybe_meta_curriculum,
            False,
            total_steps=step
        )

        trainer_factory.trainer_config['Brain'].hyperparameters.learning_rate_schedule = ScheduleType.CONSTANT
        trainer_factory.trainer_config['Brain'].hyperparameters.learning_rate = 0.0005 * (1 - counter/max_meta_updates)
        trainer_factory.trainer_config['Brain'].hyperparameters.beta = 0.005 * (1 - counter/max_meta_updates)
        trainer_factory.trainer_config['Brain'].hyperparameters.epsilon = 0.2 * (1 - counter/max_meta_updates)
        print("Current lr: {}\nCurrent beta: {}\nCurrent epsilon: {}".format(trainer_factory.trainer_config['Brain'].hyperparameters.learning_rate, trainer_factory.trainer_config['Brain'].hyperparameters.beta, trainer_factory.trainer_config['Brain'].hyperparameters.epsilon))

        # Create controller and begin training.
        tc = TrainerController(
            trainer_factory,
            write_path,
            checkpoint_settings.run_id,
            maybe_meta_curriculum,
            not checkpoint_settings.inference,
            run_seed,
            sampler_manager,
            resampling_interval,
        )
        try:
            # Get inital weights
            print("Start learning at step: " + str(step) +" meta_step: " + str(counter))
            print("Inital weights: " + str(inital_weights[8]))
            weights_after_train = tc.start_learning(env_manager, inital_weights)

            print(tc.trainers['Brain'].optimizer)

            # weights_after_train = tc.weights
            # print("Trained weights: " + str(weights_after_train[8]))
            step += options.behaviors['Brain'].max_steps
            print("meta step:" +str(step))
            # print(weights_after_train)
            # equal = []
            # for i, weight in enumerate(tc.weights):
            #     equal.append(np.array_equal(inital_weights[i], weights_after_train[i]))
            # print(all(equal))
        finally:
            print(len(weights_after_train), len(inital_weights))
            for i, weight in enumerate(weights_after_train):
                inital_weights[i] = weights_after_train[i]
            env_manager.close()
            write_run_options(write_path, options)
            write_timing_tree(run_logs_dir)
            write_training_status(run_logs_dir)
        counter += 1


def write_run_options(output_dir: str, run_options: RunOptions) -> None:
    run_options_path = os.path.join(output_dir, "configuration.yaml")
    try:
        with open(run_options_path, "w") as f:
            try:
                yaml.dump(run_options.as_dict(), f, sort_keys=False)
            except TypeError:  # Older versions of pyyaml don't support sort_keys
                yaml.dump(run_options.as_dict(), f)
    except FileNotFoundError:
        logger.warning(
            f"Unable to save configuration to {run_options_path}. Make sure the directory exists"
        )


def write_training_status(output_dir: str) -> None:
    GlobalTrainingStatus.save_state(os.path.join(output_dir, TRAINING_STATUS_FILE_NAME))


def write_timing_tree(output_dir: str) -> None:
    timing_path = os.path.join(output_dir, "timers.json")
    try:
        with open(timing_path, "w") as f:
            json.dump(get_timer_tree(), f, indent=4)
    except FileNotFoundError:
        logger.warning(
            f"Unable to save to {timing_path}. Make sure the directory exists"
        )


def create_sampler_manager(sampler_config, run_seed=None):
    resample_interval = None
    if sampler_config is not None:
        if "resampling-interval" in sampler_config:
            # Filter arguments that do not exist in the environment
            resample_interval = sampler_config.pop("resampling-interval")
            if (resample_interval <= 0) or (not isinstance(resample_interval, int)):
                raise SamplerException(
                    "Specified resampling-interval is not valid. Please provide"
                    " a positive integer value for resampling-interval"
                )

        else:
            raise SamplerException(
                "Resampling interval was not specified in the sampler file."
                " Please specify it with the 'resampling-interval' key in the sampler config file."
            )

    sampler_manager = SamplerManager(sampler_config, run_seed)
    return sampler_manager, resample_interval


def try_create_meta_curriculum(
    curriculum_config: Optional[Dict], env: SubprocessEnvManager, restore: bool = False
) -> Optional[MetaCurriculum]:
    if curriculum_config is None or len(curriculum_config) <= 0:
        return None
    else:
        meta_curriculum = MetaCurriculum(curriculum_config)
        if restore:
            meta_curriculum.try_restore_all_curriculum()
        return meta_curriculum


def create_environment_factory(
    env_path: Optional[str],
    no_graphics: bool,
    seed: int,
    start_port: Optional[int],
    env_args: Optional[List[str]],
    log_folder: str,
) -> Callable[[int, List[SideChannel]], BaseEnv]:
    def create_unity_environment(
        worker_id: int, side_channels: List[SideChannel]
    ) -> UnityEnvironment:
        # Make sure that each environment gets a different seed
        env_seed = seed + worker_id
        return UnityEnvironment(
            file_name=env_path,
            worker_id=worker_id,
            seed=env_seed,
            no_graphics=no_graphics,
            base_port=start_port,
            additional_args=env_args,
            side_channels=side_channels,
            log_folder=log_folder,
        )

    return create_unity_environment


def run_cli(options: RunOptions) -> None:
    try:
        print(
            """

                        ▄▄▄▓▓▓▓
                   ╓▓▓▓▓▓▓█▓▓▓▓▓
              ,▄▄▄m▀▀▀'  ,▓▓▓▀▓▓▄                           ▓▓▓  ▓▓▌
            ▄▓▓▓▀'      ▄▓▓▀  ▓▓▓      ▄▄     ▄▄ ,▄▄ ▄▄▄▄   ,▄▄ ▄▓▓▌▄ ▄▄▄    ,▄▄
          ▄▓▓▓▀        ▄▓▓▀   ▐▓▓▌     ▓▓▌   ▐▓▓ ▐▓▓▓▀▀▀▓▓▌ ▓▓▓ ▀▓▓▌▀ ^▓▓▌  ╒▓▓▌
        ▄▓▓▓▓▓▄▄▄▄▄▄▄▄▓▓▓      ▓▀      ▓▓▌   ▐▓▓ ▐▓▓    ▓▓▓ ▓▓▓  ▓▓▌   ▐▓▓▄ ▓▓▌
        ▀▓▓▓▓▀▀▀▀▀▀▀▀▀▀▓▓▄     ▓▓      ▓▓▌   ▐▓▓ ▐▓▓    ▓▓▓ ▓▓▓  ▓▓▌    ▐▓▓▐▓▓
          ^█▓▓▓        ▀▓▓▄   ▐▓▓▌     ▓▓▓▓▄▓▓▓▓ ▐▓▓    ▓▓▓ ▓▓▓  ▓▓▓▄    ▓▓▓▓`
            '▀▓▓▓▄      ^▓▓▓  ▓▓▓       └▀▀▀▀ ▀▀ ^▀▀    `▀▀ `▀▀   '▀▀    ▐▓▓▌
               ▀▀▀▀▓▄▄▄   ▓▓▓▓▓▓,                                      ▓▓▓▓▀
                   `▀█▓▓▓▓▓▓▓▓▓▌
                        ¬`▀▀▀█▓

        """
        )
    except Exception:
        print("\n\n\tUnity Technologies\n")
    print(get_version_string())

    if options.debug:
        log_level = logging_util.DEBUG
    else:
        log_level = logging_util.INFO
        # disable noisy warnings from tensorflow
        tf_utils.set_warnings_enabled(False)

    logging_util.set_log_level(log_level)

    logger.debug("Configuration for this run:")
    logger.debug(json.dumps(options.as_dict(), indent=4))

    # Options deprecation warnings
    if options.checkpoint_settings.load_model:
        logger.warning(
            "The --load option has been deprecated. Please use the --resume option instead."
        )
    if options.checkpoint_settings.train_model:
        logger.warning(
            "The --train option has been deprecated. Train mode is now the default. Use "
            "--inference to run in inference mode."
        )

    run_seed = options.env_settings.seed

    # Add some timer metadata
    add_timer_metadata("mlagents_version", mlagents.trainers.__version__)
    add_timer_metadata("mlagents_envs_version", mlagents_envs.__version__)
    add_timer_metadata("communication_protocol_version", UnityEnvironment.API_VERSION)
    add_timer_metadata("tensorflow_version", tf_utils.tf.__version__)


    if options.env_settings.seed == -1:
        run_seed = np.random.randint(0, 10000)
    run_training(run_seed, options)



def main():
    run_cli(parse_command_line())


# For python debugger to directly run this script
if __name__ == "__main__":
    main()
